# Architecture

AIQUANT-LITE (Hermes-OPC internally) connects an A-share research backend to a Vue workspace, CLI, MCP and desktop source modules.

## Service boundaries

| Component | Default | Responsibility |
|---|---|---|
| Router | `127.0.0.1:8765` | Owns the primary DuckDB online; hosts research, screening, scoring, decisions and audit APIs |
| Data Hub | `127.0.0.1:8766` | Source-attributed data services through the online Router boundary |
| WebUI | `127.0.0.1:8767` | Serves built Vue assets and GET-only views; proxies facts and scores to Router |
| Finance Data MCP | stdio | Bounded research tools for local clients and Hermes |

WebUI may read explicitly approved research sidecars and file artifacts, but must not directly open the primary DuckDB. Maintenance of the primary database requires stopping online owners first. Development fixtures require explicit opt-in and are disabled when `HERMES_ENV=production`.

## Scoring sources

The current score resolver first uses `cross_section_4d`, a centered rank composite over technical, fundamental, sentiment and margin inputs with available-input weighting 30/30/25/15. When given `data_cutoff`, it queries a historical section at or before that cutoff. If unavailable, it returns `legacy_60_40_fallback` with `degraded=True`.

The orchestration experiment interface separately supports legacy 60/40, three-factor 50/35/15 and custom weights. These experimental scores must not be interpreted as the same quantity as a cross-sectional rank. Sentiment features can participate in current cross-sectional and experimental paths; the older blanket statement that all sentiment inputs have formal weight zero is obsolete.

Data status, position context, percentile thresholds and Risk VETO constrain final actions. DecisionPacket records preserve frozen plan zones, evidence and versions; UI views display persisted records.

## Research workflow

```text
Observe market facts -> deterministic screening -> select a symbol
 -> source-attributed research -> PIT and risk checks
 -> explicit decision record -> human review / simulation
```

The repository contains no broker adapter or automated live-order path. Paper-account updates and manual trade facts are separate from real broker activity. Online provider availability and strategy validity require their own evidence beyond offline tests.

See [limitations](limitations.md), [API](api-reference.md) and the [publication audit](release-audit-2026-10-18.md).
