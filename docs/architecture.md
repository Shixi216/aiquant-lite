# Architecture

Hermes-OPC is a local-first A-share research system.

```text
Hermes / local client
  -> Finance MCP (bounded tool schemas)
  -> Router :8765 (workflow, scanner, orchestration, experiments)
  -> Data Hub :8766 (source-attributed data)
  -> DuckDB (audit and research records)
```

Stage 11 adds a compatibility layer; it does not remove or rename existing routes. New endpoints
use a response envelope containing `request_id`, status, data, classified error, sanitized error,
warnings, risk flags, `data_cutoff`, generation time, API version, and `research_only=true`.

The interaction boundary is parse → scan → user selection → research → explicit single-symbol
decision. Scanner output is research priority, not a recommendation. Formal action remains
technical 60% plus fundamental 40%. Sentiment, policy/news, capital flow, and the five-factor
composite remain shadow-only. Hard risk VETO is final.

There is no broker, QMT, xtquant, real-order, or automatic execution component.
