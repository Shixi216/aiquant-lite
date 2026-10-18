# API reference

Existing Router and Data Hub routes remain compatible. Stage 11 adds:

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/integration/tools` | Hermes tool schemas and risk metadata |
| POST | `/v1/integration/workflow/parse` | deterministic natural-language parse |
| POST | `/v1/integration/workflow/scan` | local 10–30 candidate scan |
| POST | `/v1/integration/workflow/research` | research up to 30; deep analysis up to ten |
| POST | `/v1/integration/workflow/decision` | one explicitly confirmed evaluation |
| POST | `/v1/integration/wecom/simulate` | local idempotent WeCom rendering |
| GET | `/v1/integration/system/health` | non-mutating health |
| GET | `/v1/integration/system/doctor` | non-mutating advice |
| GET | `/v1/integration/openapi-summary` | Router/Data Hub contract counts |
| GET | `/v1/integration/experiments/{run_id}` | existing experiment report |

Stage 11 endpoints use the unified response envelope. Errors are classified and sanitized. OpenAPI
operations have summaries, descriptions, and response schemas. No endpoint accepts arbitrary SQL
or raw database table names.

## Recent model calls

`GET /v1/audit/model-calls` reads persisted model calls, including calls without a task
and calls whose task record is missing. The task list alone does not represent all model usage.

| Parameter | Default | Constraint |
|---|---|---|
| `since` | Seven days before `until` | ISO 8601 datetime with a timezone |
| `until` | Current time | ISO 8601 datetime with a timezone, at or after `since` |
| `limit` | `100` | `1` to `1000` |

The response contains `since`, `until`, `calls`, and these counts:

- `count`: valid records returned, newest first.
- `total_count`, `success_count`, `failed_count`: all persisted records in the inclusive window,
  independent of `limit`.
- `unlinked_call_count`: records in the window without a matching task.
- `invalid_record_count`: selected records omitted because their stored values fail validation.

Counts and records share one database snapshot. Error messages are sanitized when read;
historical audit rows are preserved. Records that failed to persist cannot appear in this view.
Missing token or cost values remain unknown.

Database contention returns HTTP `503`, `Retry-After: 3`, and `error_code: DATABASE_BUSY`.
The same retryable response applies to other Router routes that encounter this typed database
error. Invalid date windows or limits return `422`.

## Automated DeepSeek review

Sentiment and policy-news escalation keep DeepSeek thinking enabled at high effort, with a
bounded completion budget of `8192` tokens. Their primary model budgets remain `800` and `1200`
respectively. Explicit Router invocation requests retain their requested token limit.
Thinking and the final answer share the completion budget; see the
[DeepSeek API contract](https://api-docs.deepseek.com/api/create-chat-completion/).

Empty or length-truncated completions remain failures. Their known model, token usage and
latency are retained by these extractors, and the error records the finish reason. Reasoning
text is never used as a final answer. Network failures retain their underlying error class;
unknown usage remains null. The existing retry and deterministic fallback limits still apply.
