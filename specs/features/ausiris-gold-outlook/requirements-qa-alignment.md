# Requirements QA Alignment: Ausiris Gold Outlook Telegram

## Gate Status

- Status: Approved
- Reviewer Notes: The selected page section, delivery channel, schedule, failure
  behavior, and message-length handling have observable acceptance criteria.
  The user approved this alignment document before implementation.

## Requirement Quality Review

| ID | Requirement | Quality | Issue | Resolution Needed |
|----|-------------|---------|-------|-------------------|
| FR-001 | Fetch the specified Ausiris page and extract the section headed “มุมมองราคาทอง” from a `div.parsys-container.parsys`. | Clear | None | None |
| FR-002 | Send the extracted text to the configured Telegram chat. | Clear | None | None |
| FR-003 | Run once at 10:00 on weekdays in Bangkok time. | Clear | Timezone derives from the project's Docker configuration. | None |
| NFR-001 | Preserve the complete text while respecting Telegram's message limit. | Clear | None | None |
| NFR-002 | Report fetch, extraction, configuration, or delivery failures without sending empty/incorrect content. | Clear | None | None |

## Acceptance Criteria

| AC ID | Source | Acceptance Criterion |
|-------|--------|----------------------|
| AC-001 | Analyst | Extract only readable text inside the matching `parsys-container parsys` containing “มุมมองราคาทอง”. |
| AC-002 | Analyst | Deliver extracted text to the configured Telegram chat. |
| AC-003 | Analyst | Split oversized content in order without losing text; each message stays within Telegram's limit. |
| AC-004 | Analyst | Missing target section or empty content sends no message and logs an error. |
| AC-005 | Analyst | HTTP/network or Telegram failure is logged and the job reports failure. |
| AC-006 | Analyst | Cron runs at 10:00 Monday–Friday in `Asia/Bangkok`. |
| AC-007 | Analyst | Missing Telegram settings are handled without exposing secrets. |

## AC to Test Case Comparison

| AC ID | Analyst Acceptance Criterion | Matching Test Case ID | Coverage | Notes |
|-------|------------------------------|-----------------------|----------|-------|
| AC-001 | Extract only the selected heading's section. | TC-001, TC-002 | Planned | Include multiple matching containers in fixture. |
| AC-002 | Deliver text to configured Telegram. | TC-003 | Planned | Use a fake notifier. |
| AC-003 | Preserve complete oversized content in chunks. | TC-004 | Planned | Assert ordering, concatenation, and length. |
| AC-004 | Empty/missing section is not sent. | TC-005 | Planned | Assert failure result and zero sends. |
| AC-005 | Fetch/send failures are reported. | TC-006, TC-007 | Planned | Assert no false success. |
| AC-006 | Schedule is weekday 10:00 Bangkok. | TC-008 | Planned | Inspect cron entry and Docker timezone. |
| AC-007 | Missing Telegram configuration is safe. | TC-009 | Planned | Assert no secrets in logs. |

## QA Traceability Matrix

| Requirement ID | AC ID | Test Case ID | Test Type | Priority | Coverage |
|----------------|-------|--------------|-----------|----------|----------|
| FR-001 | AC-001 | TC-001, TC-002 | Unit | High | Planned |
| FR-002 | AC-002 | TC-003 | Unit | High | Planned |
| NFR-001 | AC-003 | TC-004 | Unit | High | Planned |
| NFR-002 | AC-004, AC-005, AC-007 | TC-005, TC-006, TC-007, TC-009 | Unit | High | Planned |
| FR-003 | AC-006 | TC-008 | Static/config | Medium | Planned |

## Planned Test Cases

| Test Case ID | Scenario | Steps | Expected Result | Type | Priority |
|--------------|----------|-------|-----------------|------|----------|
| TC-001 | Target section and unrelated matching sections coexist. | Parse a fixture with multiple `.parsys-container.parsys` divs. | Extract only the div containing “มุมมองราคาทอง”. | Unit | High |
| TC-002 | HTML markup, entities, and nested paragraphs in target section. | Parse representative nested markup. | Return readable text with entities decoded and paragraph breaks preserved. | Unit | High |
| TC-003 | Successful worker run. | Inject HTML response and fake notifier. | Send the extracted text to the notifier. | Unit | High |
| TC-004 | Text exceeds one Telegram message. | Inject long multi-paragraph content. | Send ordered chunks within the limit; joining chunks preserves all text. | Unit | High |
| TC-005 | Target heading is absent or has no text. | Inject page fixture without usable target content. | Return failure and send nothing. | Unit | High |
| TC-006 | HTTP status error or timeout. | Inject failing HTTP client. | Return failure, log cause, and send nothing. | Unit | High |
| TC-007 | Telegram rejects message delivery. | Inject notifier returning false. | Return failure and log delivery failure. | Unit | High |
| TC-008 | Cron configuration. | Inspect cron file and Docker timezone. | One 10:00 Monday–Friday schedule uses the new wrapper. | Static/config | Medium |
| TC-009 | Telegram configuration absent. | Inject no notifier/configuration. | Send nothing and report the issue without logging credentials. | Unit | High |

## Edge Cases and Negative Tests

| ID | Scenario | Expected Handling | Covered By |
|----|----------|-------------------|------------|
| EC-001 | Several matching class containers occur on the page. | Select only the section with the requested heading. | TC-001 |
| EC-002 | Text contains HTML entities, scripts, or nested markup. | Decode visible text; exclude script/style content. | TC-002 |
| EC-003 | Text is too long for one Telegram message. | Split in order without losing text. | TC-004 |
| EC-004 | Network, parse, configuration, or send failure. | Do not report a successful delivery. | TC-005–TC-007, TC-009 |

## Open Questions

- None. The user selected the “มุมมองราคาทอง” section. Bangkok time follows the
  project's configured cron timezone.

## Implementation Readiness

- Ready for architecture: Yes
- Ready for implementation: Yes — approved by the user on 2026-10-19.
- Blocking gaps:
  - None.
