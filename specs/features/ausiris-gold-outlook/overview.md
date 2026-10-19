# Ausiris Gold Outlook Telegram: Feature Overview

## Goal

Send the text in Ausiris's “มุมมองราคาทอง” section to the configured Telegram
chat at 10:00 on weekdays.

## Confirmed Scope

- Fetch `http://www.ausiris.co.th/content/index/research/research-gold/gold-main.html`.
- Extract visible text from the `div.parsys-container.parsys` that contains the
  heading “มุมมองราคาทอง”. The page has multiple matching divs, so matching by
  the heading keeps unrelated currency, price, image, and footer content out.
- Send the extracted text through the existing Telegram notifier.
- Run at 10:00, Monday through Friday, using the project's `Asia/Bangkok`
  container timezone.
- Split text at paragraph boundaries when needed to stay within Telegram's
  message-size limit, without dropping content.
- Use Python's standard-library HTML parser and existing HTTP/Telegram
  dependencies; do not add a package dependency.

## Non-goals

- Do not send other `parsys-container parsys` sections.
- Do not summarize, translate, or otherwise rewrite Ausiris's text.
- Do not alter any trading signal, database, or other notification pipeline.

## Acceptance Criteria

- AC-001: When the page contains a `parsys-container parsys` section headed
  “มุมมองราคาทอง”, the worker extracts its readable text and excludes content
  outside that section.
- AC-002: The extracted text is delivered to the configured Telegram chat.
- AC-003: If the text exceeds Telegram's per-message limit, all text is sent in
  ordered chunks that do not exceed the limit.
- AC-004: A missing target section or empty extracted text sends no Telegram
  message and logs a useful error.
- AC-005: An HTTP failure, timeout, or Telegram delivery failure is reported;
  the scheduled worker exits unsuccessfully so operators can see the failure.
- AC-006: The cron schedule invokes the worker once at 10:00, Monday through
  Friday, in `Asia/Bangkok` time.
- AC-007: Missing Telegram configuration sends no message and reports the
  configuration issue without exposing secrets.

## Affected Areas

- New isolated HTML-fetch/extract/send worker and focused tests.
- New cron wrapper and weekday 10:00 schedule.
- Docker executable permissions and project run/configuration documentation.

## Implementation Sequence

1. Add testable extraction and message-chunking helpers using the standard
   library.
2. Add the worker using the existing notifier and bounded HTTP timeout.
3. Add its cron wrapper and Monday–Friday 10:00 entry.
4. Document manual invocation/configuration and run focused verification.
