# Requirements QA Alignment: Bollinger Wick Alert

## Gate Status

- Status: Approved
- Reviewer Notes: The supplied behavior is observable, has an explicit data
  source and time basis, and has complete critical-path test coverage planned.
  Explicit user approval remains required before implementation.

## Requirement Quality Review

| ID | Requirement | Quality | Issue | Resolution Needed |
|----|-------------|---------|-------|-------------------|
| FR-001 | Evaluate closed `XAU/USD` 5m candles from Twelve Data. | Clear | None | None |
| FR-002 | Use Bollinger period 20 and deviation 2.0. | Clear | None | None |
| FR-003 | Notify BUY after a lower-band wick breach and close inside. | Clear | None | None |
| FR-004 | Notify SELL after an upper-band wick breach and close inside. | Clear | None | None |
| FR-005 | Run every five minutes Monday–Friday in Bangkok time. | Clear | None | None |
| NFR-001 | Avoid duplicate messages; attempt a rejected Telegram delivery at most three times per invocation, then retry on a later cron run. | Clear | None | None |
| NFR-002 | Fail safely on invalid or unavailable inputs. | Clear | None | None |

## Acceptance Criteria

| AC ID | Source | Acceptance Criterion |
|-------|--------|----------------------|
| AC-001 | Analyst | Lower-band wick + in-band close produces one BUY message. |
| AC-002 | Analyst | Upper-band wick + in-band close produces one SELL message. |
| AC-003 | Analyst | Equal-band wick, outside close, or no breach produces no message. |
| AC-004 | Analyst | Duplicate run is suppressed; rejected delivery remains retryable. |
| AC-005 | Analyst | Bad data, missing configuration, or provider failure produces no false message. |
| AC-006 | Analyst | In-progress candles are excluded. |
| AC-007 | Analyst | Cron only runs Monday–Friday. |
| AC-008 | User decision | A dual-sided wick breach produces both independent messages. |

## QA Traceability Matrix

| Requirement ID | AC ID | Test Case ID | Test Type | Priority | Coverage |
|----------------|-------|--------------|-----------|-----------|----------|
| FR-001, FR-002, FR-003 | AC-001 | TC-001 | Unit | High | Planned |
| FR-001, FR-002, FR-004 | AC-002 | TC-002 | Unit | High | Planned |
| FR-003, FR-004 | AC-003 | TC-003 | Unit | High | Planned |
| NFR-001 | AC-004 | TC-004 | Unit | High | Planned |
| NFR-002 | AC-005 | TC-005 | Unit | High | Planned |
| FR-001 | AC-006 | TC-006 | Unit | High | Planned |
| FR-005 | AC-007 | TC-007 | Static/config | Medium | Planned |
| FR-003, FR-004 | AC-008 | TC-008 | Unit | High | Planned |

## Planned Test Cases

| Test Case ID | Scenario | Expected Result | Type | Priority |
|--------------|----------|-----------------|------|----------|
| TC-001 | Closed candle low < lower band and close in band. | One BUY Thai message. | Unit | High |
| TC-002 | Closed candle high > upper band and close in band. | One SELL Thai message. | Unit | High |
| TC-003 | Boundary/no-breach/outside-close cases. | No message. | Unit | High |
| TC-004 | Repeat run and Telegram rejection. | Suppress delivered duplicate; make three attempts in one invocation, then retry on the next run if all fail. | Unit | High |
| TC-005 | Invalid OHLC, missing key, provider error, no notifier. | Safe failure and no false message. | Unit | High |
| TC-006 | Provider includes a live 5m candle. | It is not used in bands or signal. | Unit | High |
| TC-007 | Cron expression and wrapper environment. | Every 5m, Mon–Fri, named lock/config present. | Static/config | Medium |
| TC-008 | One closed candle breaches both bands and closes in-band. | One BUY and one SELL message. | Unit | High |

## Edge Cases and Negative Tests

| ID | Scenario | Expected Handling | Covered By |
|----|----------|-------------------|------------|
| EC-001 | Candle high/low equals a band. | Not a breach; no alert. | TC-003 |
| EC-002 | Candle close equals a band after breach. | In-band; eligible. | TC-001, TC-002 |
| EC-003 | First provider value is live. | Exclude based on Bangkok wall clock. | TC-006 |
| EC-004 | Repeated cron fire. | Deduplicate by candle and direction. | TC-004 |
| EC-005 | API/Telegram interruption. | No false state advancement; try three times, then retry delivery later. | TC-004, TC-005 |
| EC-006 | One candle has upper and lower wick breaches. | Send both direction-specific alerts. | TC-008 |

## Open Questions

- None. Telegram is the repository's established notification channel; the
  user specified Thai content and the project already configures Telegram.

## Implementation Readiness

- Ready for architecture: Yes
- Ready for implementation: Yes — approved by the user on 2026-09-11.
- Blocking gaps:
  - None.
