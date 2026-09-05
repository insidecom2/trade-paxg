# MySQL Price Alert: Feature Overview

## Goal

Send an informational Telegram alert when the close of the latest completed
4-hour candle crosses a support or resistance level stored for the current
Bangkok-local calendar date.

## Confirmed Scope

- Read `price_alert` records whose `date` is within today in `Asia/Bangkok`
  and whose `symbol` is `XAUUSD`.
- A day can contain multiple records, and every populated support or resistance
  value is independently evaluated.
- Fetch `XAU/USD` 4-hour candles through Twelve Data, and evaluate only the
  latest completed candle's `close`.
- Send Thai Telegram messages for a close above resistance or below support.
- Send a separate Thai rejection alert when the high touches/exceeds resistance
  but the close is at or below it, or when the low touches/falls below support
  but the close is at or above it.
- Deliver at most one alert per `(level, direction, 4-hour candle)`.
- Run from a cron job.

## Non-goals

- Do not place orders or change the existing strategy decision flow.
- Do not calculate, insert, update, or delete support/resistance levels.
- Do not alert from a live/in-progress candle or current ticker price.

## Acceptance Criteria

- AC-001: Given multiple eligible rows for today and the configured symbol,
  when the latest completed 4-hour close exceeds a non-null resistance, the
  system sends one Telegram alert containing that level and closing price.
- AC-002: Given multiple eligible rows for today and the configured symbol,
  when the latest completed 4-hour close is below a non-null support, the
  system sends one Telegram alert containing that level and closing price.
- AC-003: Given a close equal to a level, the system sends no breakout alert.
- AC-004: Given the same level, direction, and 4-hour candle is processed
  again, the system sends no duplicate alert.
- AC-005: Given a new completed 4-hour candle that still crosses the level,
  the system may send one new alert for that candle.
- AC-006: Given rows outside the half-open Bangkok date range or with a symbol
  other than `XAUUSD`, the system does not evaluate them.
- AC-007: Given no eligible rows, missing/invalid level values, unavailable
  MySQL data, unavailable candle data, or disabled Telegram configuration, the
  job fails safely: it sends no false alert and logs the reason.
- AC-008: Given a candle high at/above resistance and close at/below it, the
  system sends one resistance-touch alert, not a breakout alert.
- AC-009: Given a candle low at/below support and close at/above it, the
  system sends one support-touch alert, not a breakdown alert.

## Intended Affected Areas

- New MySQL read-only repository/configuration (with the database name and
  credentials supplied through environment variables).
- New 4-hour price-alert job and focused tests.
- Existing market-data manager, Telegram notifier, and lock-safe state store.
- Cron wrapper/schedule and environment documentation.

## Design Decisions

- `price_alert` is the table name; `date` is a `DATETIME` column.
- The query uses `date >= start_of_today_bangkok` and
  `date < start_of_tomorrow_bangkok`, rather than applying `DATE()` to the
  column, to preserve a possible index on `date`.
- `symbol` is matched as `XAUUSD`, independently of the exchange symbol used
  to fetch the candle (`XAU/USD` through Twelve Data).
- MySQL connection settings use environment variables and are never committed.
