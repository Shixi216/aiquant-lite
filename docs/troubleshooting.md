# Troubleshooting

- `NOT_READY` for Data Hub: start the local service on port 8766, then rerun health.
- `DEGRADED` scanner: refresh the market snapshot through the existing controlled data process;
  do not treat stale rankings as current.
- WeCom `NOT_CONFIGURED`: use local simulation or configure the external channel separately.
  Core research remains available.
- `INSUFFICIENT_COVERAGE`: collect point-in-time fundamentals; do not fill missing values with
  zero and do not claim a formal full-market 60/40 result.
- Decision confirmation error: select exactly one symbol and send
  `CONFIRM_DECISION:<symbol>`.
- Migration failure: stop and compare 0100–0111 checksums. Doctor will not repair or modify them.

Sanitized errors intentionally omit tokens, local paths, stack traces, and internal reasoning.
