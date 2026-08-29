# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A PAXG/XAUUSD trading signal bot. It fetches OHLCV candles, runs a support/resistance +
supply/demand-zone strategy (`analyzer.py`), tracks signal state across runs
(`trading_state.py`), and pushes results to Telegram. It runs three ways: as a one-shot
CLI script (`main.py`), as a FastAPI service (`api.py`) that schedules the same work as a
background task, and as scheduled cron jobs inside the Docker image.

## Commands

```bash
# Install deps (project uses a local .venv)
pip install -r requirements.txt

# Run all tests (stdlib unittest, not pytest)
python -m unittest discover -p "test_*.py" -v

# Run a single test file / class / method
python -m unittest test_analyzer_strategy -v
python -m unittest test_analyzer_strategy.SomeTestClass -v
python -m unittest test_analyzer_strategy.SomeTestClass.test_something -v

# Run one signal analysis manually (writes to trading_state.json, sends Telegram msg)
python main.py --symbol PAXG/USDT --tf 4h

# Run the exit-profit check manually
python exit_profit.py --symbol PAXG/USDT

# Run the API locally
uvicorn api:app --host 0.0.0.0 --port 8002

# Docker: run the API
docker build -t trade-paxg-api .
docker run --rm --env-file .env -p 8002:8002 trade-paxg-api

# Docker Compose: runs the cron container (1h exit-profit check + 4h strategy, Mon-Fri)
docker compose up --build -d
```

There is no linter/formatter configured in this repo.

## Architecture

**Two independent pipelines share the same market data and notifier, but do not share
state:**

1. **Strategy signals** (`main.py` → `analyzer.py`): entry signals (BUY/SELL/HOLD) based
   on support/resistance breakouts, supply/demand zones, trend (EMA), volume, candle
   patterns, and (on 4h only) Bollinger Bands. Persists state under key
   `"{symbol}|{timeframe}"` in `trading_state.json`.
2. **Exit-profit monitoring** (`exit_profit.py`): once a strategy run produces an
   `entry_price`, it's saved under key `"{symbol}|exit_profit"`. This pipeline checks the
   live price against that stored entry/stop/take-profit zone and sends its own
   notifications (`exit_profit_notification.py`) — separate cadence, separate cron job,
   separate lock file.

**`exchange_manager.py`** abstracts market data behind `create_market_data_manager()`,
selected via `PRICE_SOURCE` env var:
- `binance` (default): `BinanceManager` uses `ccxt`, automatically failing over between
  `api.binance.com` and the `data-api.binance.vision` mirror (Binance is geo-blocked in
  some regions).
- `mysql`: `MySQLManager` reads an existing hourly XAUUSD price table
  (`f_symbol='xauusd'`, `f_price`) and aggregates hourly rows into 1h/4h/1d candles with
  zero volume. Table/column names are validated against a safe-identifier regex before
  being interpolated into SQL.

**`trading_state.py`** (`TradingStateStore`) is a `flock`-guarded JSON key/value store
(`trading_state.json`). Read locks are shared, writes are exclusive with atomic
tmp-file-then-rename. This lets the hourly and 4-hourly cron jobs run concurrently
without corrupting state or racing each other — but each *type* of state (strategy vs.
exit-profit) uses its own key namespace, not its own file.

**Candle-timeframe integrity**: `main.py` validates that fetched candles actually match
the requested timeframe (`validate_candle_timeframe`) and restricts analysis to candles
inside the approximate XAUUSD trading session (Sun 22:00 UTC–Fri 22:00 UTC via
`prepare_analysis_candles`), since PAXG trades 24/7 but the strategy is tuned to gold
market hours.

**Level continuity across runs** (`resolve_dynamic_levels` in `main.py`): once a
breakout/breakdown is in a pending state (watch/confirmed/retest), the previous
support/resistance levels are deliberately *not* recalculated — recomputing mid-sequence
would move the reference and lose the pending confirmation. New levels are only derived
via `analyzer.find_dynamic_levels` once a state resolves back to neutral.

**Zone tolerance**: `calculate_timeframe_zone_tolerance` in `main.py` picks an ATR
multiplier per timeframe (`ZONE_ATR_MULTIPLIERS`, default 0.75, 1h override 0.30) and, for
1h only, caps the zone width at `ONE_HOUR_MAX_ZONE_POINTS` (1000 gold points).

**`api.py`** just fires `main.main()` / `exit_profit.run_standalone()` as FastAPI
`BackgroundTasks` and returns 202 immediately — no job queue or result tracking; check
logs/Telegram for outcomes.

**Liquidity-sweep watch** (`liquidity_sweep.py`) is a third, independent pipeline,
**notification-only — it never places orders**: a 1h state machine
(`IDLE → NEAR_ZONE → SWEPT → [CONFIRMING] → ENTERED`) restricted to 12:00–21:00 Bangkok
time, Mon–Fri. On a close back past a swept level, `CONFIRMATION_CANDLES` controls how
many candles must hold that reclaim before it counts as a setup — at `1` (the current
default) the close-back candle itself is enough and entry is attempted immediately;
higher values route through an explicit `CONFIRMING` phase waiting for more candles to
hold. Entry then requires two more gates: `detect_reversal_candle` — the entry candle
must be a genuine color flip against the prior candle (red→green for BUY, green→red for
SELL) — and `MINIMUM_RISK_REWARD` against the nearest opposite-side zone as the
take-profit target. Any setup that fails either gate is skipped, not entered. Stop-loss
sits `STOP_LOSS_ATR_BUFFER` (0.75×ATR) beyond the sweep extreme, widened from an initial
0.25× after live observation that price often runs to clear a tight stop before the real
reversal. It tracks progress through `trading_state.json` under key
`"{symbol}|liquidity_sweep"`, sends a Telegram update at every phase transition (not just
on entry), and on a confirmed entry also writes `"{symbol}|exit_profit"` so the existing
exit-profit monitor picks up managing that trade. Key zones come from today's Asian
(00:00–08:00 UTC) and London (08:00–16:00 UTC, session-to-date since the watch window
starts mid/late-London) session highs/lows plus `analyzer.find_key_levels` on 1h candles.
`is_within_notification_window` gates the whole check, so running it outside the window
(manually or via a misfired cron) is a safe no-op.

A correctness detail worth knowing if you touch this module: session-high/low zones
(`session_high_low`) must be built from candles *before* the one being tested
(`candles_established_before_latest`), or a session high/low just tracks the current
price and can never be meaningfully "swept." `_expires_at` derives its timestep from
`CANDLE_INTERVAL_MS` (looked up from `STRATEGY_TIMEFRAME` via `TIMEFRAME_MINUTES`) rather
than a hardcoded interval — keep that in sync if `STRATEGY_TIMEFRAME` ever changes again.

This module went through several backtested iterations (see git history) before landing
here: a full CHoCH/Displacement/FVG confirmation flow (too few signals to evaluate, 1
trade/30 days); instant entry on close-back on 5m/15m (3.4–16.7% win rate, net negative
R over 90 days — most close-backs are shallow fakeouts, not real reversals); 1h with a
narrow 18:00–21:00 window (0 trades/90 days — too few in-window ticks for a sweep to
resolve before the window closes); 1h widened back to 12:00–21:00 plus a
reversal-candle-color gate (1 trade/90 days, -1R). None of these samples are large
enough to call the strategy validated either way. Since this is notification-only, an
unresolved backtest is accepted rather than chased further with a trend filter —
**treat every alert as a prompt to look at the chart, not a validated signal.** Re-run
`backtest_liquidity_sweep.py` after any parameter change here.

**`backtest_liquidity_sweep.py`** replays the same `advance_liquidity_sweep_state`
function (imported from `liquidity_sweep.py`, not reimplemented) against paginated
historical Binance candles, tick-by-tick, only at timestamps that fall inside the
notification window — so it exercises the exact production code path offline (no
Telegram, no `trading_state.json` writes) and reports a funnel of where setups get
invalidated plus win/loss/R-multiple stats for any completed entries.

**AI Gold Trading Analyst** (`ai_analysis.py`) is a fourth, independent pipeline —
**notification-only, no news integration in this increment**: once a day at 08:00
Bangkok it builds a `DAILY_OUTLOOK` (bias, confidence, preferred strategy, S/R zones,
bullish/bearish scenarios, invalidation) from indicators `analyzer.py` already computes
(EMA trend, ATR, Bollinger, volume ratio, key levels, supply/demand zones) plus
previous-day and Asian-session high/low reused from `liquidity_sweep.py`
(`session_high_low`, `bangkok_now`, `ASIAN_SESSION_HOURS_UTC`), and sends the result to
OpenAI's Responses API (`ai_client.py`, `client.responses.parse` with a pydantic
`text_format` — Structured Outputs, so the model's output is schema-validated by
construction, not parsed as free text). The prompt is centralized in `ai_prompts.py`
(`AI_SYSTEM_PROMPT`); it explicitly instructs the model to treat all supplied market/news
data as untrusted content, never as instructions, and to never invent missing
prices/indicators/news. Economic news is out of scope for this increment — the request
always marks `news_available=False` rather than silently omitting the topic.

Gated by `AI_ANALYSIS_ENABLED` (default `false`) and requires `OPENAI_API_KEY`; either
missing causes a clean skip (`ai.analysis.skipped`), not a crash — this pipeline can
never affect the strategy, exit-profit, or liquidity-sweep pipelines. A schema-invalid or
failed OpenAI response is retried once, then logged and dropped (no Telegram send, no
garbage signal). Tracks state under `"{symbol}|ai_daily_outlook"` in `trading_state.json`
keyed by Bangkok-local date + `status: "sent"`, so a duplicate cron fire or manual
re-run the same day is a no-op; a prior `"failed"` run is retried on the next invocation
rather than permanently skipped. `is_uptrend`/`is_downtrend` need 200+ candles for a
valid EMA200, so this pipeline fetches 250 4h/1h candles for indicator accuracy even
though only the computed `MarketSnapshot` (not raw candles) is ever sent to OpenAI — kept
that way deliberately to control token usage. No auto-trading: this ever only sends a
Telegram notification, exactly like the other three pipelines.

## Cron / deployment layout

`trade-paxg.cron` (installed into the Docker image, `CRON_TZ=Asia/Bangkok`) runs, Mon–Fri:
- `run_exit_profit_job.sh` hourly, guarded by `flock -n /tmp/trade-paxg-exit.lock`
- `run_cron_job.sh 4h` every 4 hours, guarded by its own lock
- `run_liquidity_sweep_job.sh` hourly from 12:00–21:00 Bangkok, guarded by
  `/tmp/trade-paxg-liquidity-sweep.lock`
- `run_ai_daily_outlook_job.sh` once at 08:00 Bangkok, guarded by
  `/tmp/trade-paxg-ai-daily-outlook.lock`

All cron entries are currently commented out in `trade-paxg.cron` (notifications
disabled deployment-wide); run the shell scripts manually when needed.

All shell scripts run as the `app` user (not root) and manually copy only the needed
`TELEGRAM_*`/`TRADING_*`/`MYSQL_*`/`PRICE_SOURCE` vars (plus `OPENAI_*`/
`AI_ANALYSIS_ENABLED` for `run_ai_daily_outlook_job.sh`) out of `/proc/1/environ` —
Debian cron starts jobs with a minimal environment, so this is how the container's
`env_file` vars reach the job. `run_cron_job.sh` accepts a positional timeframe argument
that overrides `TRADING_TIMEFRAME`.

`docker-compose.yml` currently only enables the cron container; the API service block is
commented out.
