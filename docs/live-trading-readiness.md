# Live-trading readiness gates

`aiquant-lite` is currently a research and paper-analysis system. Passing provider connectivity
checks does not make it safe to control a real-money brokerage account.

Every gate below must be implemented, tested, and independently reviewed before live orders are
enabled:

1. **Separation of duties** — research, signal approval, order construction, and broker execution
   must be separate components with narrow credentials.
2. **Paper-trading burn-in** — run the complete strategy and operations path for at least 30
   trading days with recorded slippage, rejects, latency, outages, and reconciliation differences.
3. **Deterministic order validation** — enforce symbol allowlists, market sessions, lot size,
   price bands, maximum order notional, maximum position, concentration, turnover, and daily loss.
4. **Human approval** — require an authenticated approval for new strategies, high-risk signals,
   exceptional order sizes, and any rule override. An LLM review is not human approval.
5. **Idempotency and replay safety** — every order intent needs a stable client order ID; retries
   must never create duplicate orders.
6. **Kill switch** — provide a tested, non-LLM emergency stop that blocks new orders and can cancel
   open orders without relying on the research stack.
7. **Broker reconciliation** — continuously compare local intents, broker acknowledgements,
   executions, cash, positions, fees, and corporate actions. Unknown differences must halt trading.
8. **Failure containment** — define behavior for stale data, conflicting sources, provider outage,
   network partition, clock drift, database failure, and partial broker responses. Fail closed.
9. **Immutable audit trail** — persist decision inputs, evidence, policy versions, approvals, order
   requests, broker responses, executions, and operator actions with synchronized timestamps.
10. **Secret isolation** — broker trading credentials must never be placed in model prompts,
    general-purpose agent environments, source control, logs, or the Hermes messaging runtime.
11. **Operational controls** — alerts, on-call ownership, incident runbooks, backup restoration,
    credential rotation, dependency pinning, and rollback drills must be exercised before launch.
12. **Legal and broker review** — confirm account permissions, exchange rules, data licences,
    automation terms, taxes, reporting, and applicable regulation for the deployment jurisdiction.

Live enablement should require a versioned readiness report signed by the system owner and an
independent reviewer. Until then, all order adapters must default to disabled or paper mode.
