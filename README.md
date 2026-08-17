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
