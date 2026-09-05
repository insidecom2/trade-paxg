# Requirements QA Alignment: MySQL Price Alert

## Gate Status

- Status: Approved
- Reviewer Notes: The supplied schema and behavior are observable and can be
  covered with isolated repository, job, and notification tests.

## Requirement Quality Review

| ID | Requirement | Quality | Issue | Resolution Needed |
|----|-------------|---------|-------|-------------------|
| FR-001 | Read today's `price_alert` levels for `XAUUSD`. | Clear | None | None |
| FR-002 | Evaluate every populated support/resistance against the latest completed `XAU/USD` 4H close from Twelve Data. | Clear | None | None |
| FR-003 | Notify Telegram once per level, direction, and closed 4H candle. | Clear | None | None |
| FR-004 | Run on cron without changing trading behavior. | Clear | None | None |
| NFR-001 | Do not commit MySQL or Telegram secrets. | Clear | None | None |
| NFR-002 | External dependency failures must not create false alerts or crash unrelated pipelines. | Clear | None | None |
| EC-001 | A `DATETIME` date filter observes the Bangkok-local day boundary. | Clear | None | None |

## Acceptance Criteria

### FR-001 to FR-004

| AC ID | Source | Acceptance Criterion |
|-------|--------|----------------------|
| AC-001 | Analyst | A close above each eligible resistance produces one alert. |
| AC-002 | Analyst | A close below each eligible support produces one alert. |
| AC-003 | Analyst | A close equal to either level produces no alert. |
| AC-004 | Analyst | Re-running for the same close, level, and direction produces no duplicate. |
| AC-005 | Analyst | A later completed 4H candle may produce a new alert for a continuing cross. |
| AC-006 | Analyst | Other symbols and out-of-range `DATETIME` records are excluded. |
| AC-007 | Analyst | Empty, invalid, unavailable, or disabled dependencies fail safely without a false alert. |
| AC-008 | User | A high at/above resistance with a close at/below it sends one resistance-touch alert. |
| AC-009 | User | A low at/below support with a close at/above it sends one support-touch alert. |

## AC to Test Case Comparison

| AC ID | Analyst Acceptance Criterion | Matching Test Case ID | Coverage | Notes |
|-------|------------------------------|-----------------------|----------|-------|
| AC-001 | Resistance breakout alert | TC-001 | Covered | Unit job test |
| AC-002 | Support breakdown alert | TC-002 | Covered | Unit job test |
| AC-003 | Equality produces no alert | TC-003 | Covered | Unit job test |
| AC-004 | Same candle is deduplicated | TC-004 | Covered | State integration test |
| AC-005 | New candle is eligible | TC-005 | Covered | State integration test |
| AC-006 | Date/symbol query filter | TC-006 | Covered | Repository query test |
| AC-007 | Dependency/invalid-data failure | TC-007 | Covered | Unit failure-path tests |
| AC-008 | Resistance touch/rejection | TC-008 | Covered | Unit job test |
| AC-009 | Support touch/hold | TC-009 | Covered | Unit job test |

## QA Traceability Matrix

| Requirement ID | AC ID | Test Case ID | Test Type | Priority | Coverage |
|----------------|-------|--------------|-----------|----------|----------|
| FR-001 | AC-006 | TC-006 | Unit | High | Covered |
| FR-002 | AC-001, AC-002, AC-003 | TC-001, TC-002, TC-003 | Unit | High | Covered |
| FR-003 | AC-004, AC-005 | TC-004, TC-005 | Unit | High | Covered |
| NFR-002 | AC-007 | TC-007 | Unit | High | Covered |
| FR-002 | AC-008, AC-009 | TC-008, TC-009 | Unit | High | Covered |

## Planned Test Cases

| Test Case ID | Scenario | Steps | Expected Result | Type | Priority |
|--------------|----------|-------|-----------------|------|----------|
| TC-001 | Resistance close | Inject a resistance below a completed close. | One Thai breakout message is sent. | Unit | High |
| TC-002 | Support close | Inject a support above a completed close. | One Thai breakdown message is sent. | Unit | High |
| TC-003 | Equality | Set close equal to each level. | No message is sent. | Unit | High |
| TC-004 | Duplicate run | Run twice for the same level/direction/candle. | Only first run sends. | Unit | High |
| TC-005 | Later candle | Process a new closed-candle timestamp. | One new alert may send. | Unit | High |
| TC-006 | MySQL selection | Verify parameterized `XAUUSD` and Bangkok date-range query. | Only eligible database rows are returned. | Unit | High |
| TC-007 | Safe failure | Simulate MySQL, market-data, notifier, and invalid-row failures. | No false message; error is logged/contained. | Unit | High |
| TC-008 | Resistance touch | Set high at/above resistance and close at/below it. | One resistance-touch message is sent. | Unit | High |
| TC-009 | Support touch | Set low at/below support and close at/above it. | One support-touch message is sent. | Unit | High |

## Edge Cases and Negative Tests

| ID | Scenario | Expected Handling | Covered By |
|----|----------|-------------------|------------|
| EC-001 | Bangkok midnight | Use half-open local-day bounds converted for MySQL. | TC-006 |
| EC-002 | Null/non-numeric level | Skip it and log a warning. | TC-007 |
| EC-003 | In-progress final candle | Exclude it before comparison. | TC-001, TC-002 |
| EC-004 | MySQL unavailable | Job ends safely and no alert is sent. | TC-007 |

## Open Questions

- None. MySQL database name, host, port, user, and password are deployment
  values supplied via environment variables, not product behavior.

## Implementation Readiness

- Ready for architecture: Yes
- Ready for implementation: No — explicit user approval of this gate is
  required by the project workflow.
- Blocking gaps:
  - Approval of this requirements QA document.
