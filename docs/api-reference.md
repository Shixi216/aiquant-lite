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

New endpoints use the unified response envelope. Errors are classified and sanitized. OpenAPI
operations have summaries, descriptions, and response schemas. No endpoint accepts arbitrary SQL
or raw database table names.
