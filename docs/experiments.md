# Experiments and evaluation

Experiments are research-only. Observations are separated from 1/3/5/20-trading-day future-return
labels. Signals use only information available at their explicit cutoff; labels are calculated
after the signal and do not flow back into ranking.

Historical replay uses RAW prices and remains exposed to limited history, survivorship bias,
corporate-action risk, missing historical features, and incomplete benchmark coverage. Formal
60/40 full-market comparison is currently `INSUFFICIENT_COVERAGE` because point-in-time
fundamentals are sparse.

No experiment changes production weights, actions, VETO, DecisionPackets, Paper Trading, or
orders. Current results cannot prove stable or causal profitability.
