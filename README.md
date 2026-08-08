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
