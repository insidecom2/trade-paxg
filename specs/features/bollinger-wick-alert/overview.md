# Bollinger Wick Alert: Feature Overview

## Goal

Send an informational Thai Telegram alert for a closed five-minute `XAU/USD`
candle when its wick breaches a Bollinger Band and its close returns inside the
band. Market data comes from Twelve Data using `TWELVEDATA_API_KEY`.

## Confirmed Scope

- Fetch `XAU/USD` five-minute OHLC candles from Twelve Data in `Asia/Bangkok`.
- Calculate Bollinger Bands from the latest closed candle's 20 closing prices,
  using a standard-deviation multiplier of 2.0.
- Send `BUY` when the closed candle's `low` is strictly below the Lower Band
  and its `close` is within the two bands, inclusive.
- Send `SELL` when the closed candle's `high` is strictly above the Upper Band
  and its `close` is within the two bands, inclusive.
- If one closed candle meets both conditions, send both independent messages.
- Run every five minutes, Monday through Friday, in Bangkok time, two minutes
  after each five-minute candle boundary so the provider can finalize the bar.
- Use the existing Telegram notifier and lock-safe state store; notify once per
  direction and closed candle. For one eligible signal, try Telegram delivery
  at most three times in that invocation; retry a notification that remains
  rejected on a later cron invocation.
- Use these exact Thai messages:
  `BUY: แท่ง 5 นาทีที่ปิดแล้วมี low ทะลุ Lower Band แต่ราคาปิดกลับเข้ากรอบ`
  and
  `SELL: แท่ง 5 นาทีที่ปิดแล้วมี high ทะลุ Upper Band แต่ราคาปิดกลับเข้ากรอบ`.

## Non-goals

- Do not place or manage orders.
- Do not change the existing strategy signal, MySQL level alert, or AI pipeline.
- Do not alert from an in-progress candle, or use current ticker price.
- Do not persist market data or change the database schema.

## Acceptance Criteria

- AC-001: Given at least 20 completed five-minute `XAU/USD` candles, when the
  latest candle low is below its Lower Band and its close is in the band,
  send exactly one Thai `BUY` Telegram notification for that candle.
- AC-002: Given at least 20 completed five-minute `XAU/USD` candles, when the
  latest candle high is above its Upper Band and its close is in the band,
  send exactly one Thai `SELL` Telegram notification for that candle.
- AC-003: Given a wick equal to a band, a close outside a band, or no wick
  breach, send no notification for that direction.
- AC-004: Given the same direction and closed candle are checked again, send
  no duplicate notification; if Telegram rejects delivery, attempt three sends
  in that invocation and leave it eligible for a later cron retry if all fail.
- AC-005: Given fewer than 20 completed candles, malformed OHLC data, an API
  failure, unavailable API key, or disabled Telegram configuration, fail safely:
  send no false notification and log the cause.
- AC-006: Given Twelve Data also returns an in-progress candle, exclude it
  before calculating bands and evaluating the wick.
- AC-007: Given a cron invocation on Saturday or Sunday, it does not run.
- AC-008: Given one closed candle breaches both bands by its wicks and closes
  in the band, the system sends one BUY and one SELL notification.

## Affected Areas

- Twelve Data timeframe mapping and its focused tests.
- A new notification-only Bollinger wick worker, shell wrapper, cron schedule,
  configuration documentation, and focused unit tests.
- Existing Telegram notifier and trading state store only through their public
  interfaces.

## Implementation Sequence

1. Add `5m` support to the Twelve Data manager and prove its request mapping.
2. Add the isolated closed-candle Bollinger wick worker and unit tests.
3. Add its minimal cron wrapper and guarded Monday–Friday five-minute entry.
4. Document the independent Twelve Data configuration and run targeted tests.
