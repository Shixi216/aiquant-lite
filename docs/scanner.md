# Scanner

The scanner converts natural language into a validated `ScannerQueryPlan` and evaluates the local
full-market snapshot in batch.

- candidate count: 10–30 through the Stage 11 workflow;
- network requests: 0;
- model calls: 0;
- automatic decision calls: 0;
- order creation: 0;
- `is_trade_recommendation=false`.

Cold scans should complete within 15 seconds and hot-cache scans within 3 seconds on the accepted
local baseline. Stale snapshots produce `DEGRADED`, never a hidden refresh or a recommendation.
The query compiler does not accept arbitrary SQL, Python expressions, table names, or internal
request-budget fields.
