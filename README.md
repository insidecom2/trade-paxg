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
