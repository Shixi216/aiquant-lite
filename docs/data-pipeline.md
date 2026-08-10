# Data pipeline

Structured provider results enter source-attributed raw records and are normalized into canonical
market or historical records. Historical daily bars use explicit `RAW` adjustment; reports must
not mix forward- or backward-adjusted prices.

Operational coverage and historical analysis use different cutoffs:

- operational freshness uses the latest completed trading date and the latest persisted snapshot;
- historical replay uses an explicit point-in-time `analysis_data_cutoff`;
- `fetched_at` is never a substitute for market availability time.

Stage 11 query and scan paths do not fetch per symbol and do not write data. Candidate research
may persist a bounded snapshot only when explicitly requested and confirmed. Provider failures,
partial coverage, stale snapshots, missing fundamentals, and conflicts are returned as warnings
or risk flags rather than filled with invented values.
