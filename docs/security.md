# Security

Keep `.env` ignored and never copy it into reports or checkpoints. APIs, MCP tools, health,
doctor, logs, and WeCom simulation expose only credential presence booleans. Errors remove secret
assignments, bearer credentials, local paths, stack traces, and hidden reasoning.

The Finance MCP allowlist has no arbitrary SQL, raw-table, broker, order, QMT, or xtquant tool.
Scanner and query tools are read-only. Research persistence is bounded to ten selected symbols
and requires explicit confirmation. Decision evaluation requires a second explicit confirmation
and cannot create orders.

Manual external-trade recording remains a separate preview-and-confirm ledger. It is not evidence
of broker execution.
