# trade-paxg

## Run the API with Docker

Build the production image:

```bash
docker build -t trade-paxg-api .
```

Run it on port 8002:

```bash
docker run --rm --env-file .env -p 8002:8002 trade-paxg-api
```

Or start it with Compose on host port 8082:

```bash
docker compose up --build -d
```

Compose also starts a cron container that runs the `1h` strategy every hour and
the independent `4h` strategy every four hours, Monday through Friday, in the
configured cron timezone. When both schedules overlap, both calculations run.
Set `TRADING_SYMBOL` in `.env` to change the scheduled symbol.

The cron container also runs a liquidity-sweep watch on the `1h` timeframe, restricted
to 12:00–21:00 Bangkok time, Monday through Friday. It waits for a session/key-level
sweep, a close back past the level, and a candle-color flip confirming the reversal
(red→green for a BUY setup, green→red for a SELL setup); requires the resulting
risk:reward against the nearest opposite-side zone to clear a minimum bar; and sends a
Telegram update at each step. **This bot only notifies — it never places orders.**
Run it manually with:

```bash
python liquidity_sweep.py --symbol PAXG/USDT
```

Before enabling the cron schedule, backtest it against recent Binance history:

```bash
python backtest_liquidity_sweep.py --symbol PAXG/USDT --days 90
```

This replays the same state machine tick-by-tick (only inside the 12:00-21:00 Bangkok
window) over historical hourly candles, reports the funnel (how many setups reached each
step vs. got invalidated), and simulates each triggered entry's outcome (SL/TP hit,
win rate, average R multiple) without sending Telegram messages or touching
`trading_state.json`.

Every configuration tried so far (5m/15m instant entry, 1h with a narrow window, 1h with
a reversal-candle gate) has produced too few trades per 90 days, or a negative total R,
to call this a validated edge — the notifications are informational only. Re-run the
backtest after any parameter change. Treat each alert as a
prompt to look at the chart yourself, not a signal to act on automatically.

Market data for both strategy analysis and exit-profit checks can be switched
between Binance and the existing MySQL price table with `PRICE_SOURCE`.
Binance is the default. For MySQL, set `MYSQL_HOST`,
`MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, and
`MYSQL_PRICE_TABLE`. MySQL rows are filtered by `f_symbol = 'xauusd'` and use
`f_price` as the hourly price. The MySQL adapter aggregates hourly prices into
`1h`, `4h`, or `1d` candles with zero volume.

Trigger an analysis:

```bash
curl -X POST http://localhost:8082/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"PAXG/USDT","timeframe":"15m"}'
```

Run the exit-profit check:

```bash
curl -X POST http://localhost:8082/exit-profit \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"PAXG/USDT"}'
```
Notification 
