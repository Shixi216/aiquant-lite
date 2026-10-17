# Current limitations

- Public source code does not include licensed datasets, personal databases, credentials or private research artifacts. A fresh clone may show missing-data states until its own sources and data are configured.
- Point-in-Time coverage depends on the provenance and availability semantics of each dataset. Current constituent membership does not establish historical membership, and current fundamentals cannot stand in for historical disclosures.
- Cross-sectional rank scores, legacy absolute-score fallbacks and orchestration experiment scores have different meanings. Inspect `score_source`, cutoff and degradation status before comparison.
- Sentiment and margin inputs now participate in the cross-sectional path; the legacy claim that all sentiment and capital inputs have zero formal weight is obsolete. Other experimental research outputs retain their own declared status.
- Research ranking is a prioritization tool. A high score is not a validated trading edge or a guarantee of execution quality.
- Freshness depends on the configured local collection process. Missing, stale and failed data must remain visible.
- Historical experiments remain exposed to survivorship, corporate actions, benchmark selection, data coverage, multiple testing and limited-window risks.
- UI fixture samples illustrate contracts and layout only. Production disables fixture mode. Some research views need separately generated sidecar databases or files.
- Provider and model adapters require suitable credentials, permissions, quotas and service availability. Code presence does not prove live connectivity. Jinniu history remains an optional experimental integration.
- The source contains a Vue workspace and PySide6 desktop modules. This publication does not certify a downloadable installer, packaged desktop release or unattended production deployment.
- WeCom adapters and Hermes integration need their own deployment and connectivity checks.
- Services default to loopback. Authentication and network controls must be configured before remote access.
- Local simulation and user-entered external trade facts do not connect to a broker. The public repository has no automated live-order path.
- Offline tests and build checks do not establish profitability, live-market reliability or future performance.
- Historical packet compatibility must be verified against a copy of existing data before migration. The publication audit uses fresh temporary databases; older packets without preserved timestamp representations still need independent checks across time zones.
- Long combined desktop/API pytest processes crashed natively in the reviewed Windows / Python 3.11.1 environment. The supplied offline runner and CI use separate processes per module; the combined-process crash cause remains unresolved.
