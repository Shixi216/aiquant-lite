# Five-factor orchestration

The five views are technical, fundamental, sentiment, policy/news, and capital flow. Evidence is
deduplicated by source record and event cluster before the shadow composite is calculated.

SCREENING and RESEARCH may display all five views. DECISION keeps two outputs separate:

- formal: technical 60% + fundamental 40%;
- shadow: five-factor composite with formal strategy weight 0.

Missing fundamental data is not replaced by zero. Insufficient point-in-time fundamental
coverage degrades formal full-market evaluation. Sentiment, policy/news, and capital flow cannot
change BUY, SELL, HOLD, or hard VETO. The Stage 11 decision endpoint requires an exact
`CONFIRM_DECISION:<symbol>` confirmation and one symbol.
