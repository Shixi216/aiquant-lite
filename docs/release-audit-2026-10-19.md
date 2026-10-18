# Research evidence publication — 2026-10-19

This publication turns an approved local inventory into a public research evidence hub. It adds eight curated topics, an index, a dated aggregate JSON summary, a vector evidence panel and updated Chinese / English project introductions. It changes documentation and presentation assets only, based on public source commit `53022f7262ecc08b6759b41fab3a723c178cd92b`.

## What demonstrates the project's depth

| Evidence | Verified scope |
|---|---|
| 2,782,220 standardized historical bar rows | Existing Router owner API table count |
| 2,625,560 Replay feature packets | Read-only sidecar count; business dates 2024-08-01 through 2026-08-13 |
| 2,238,764 aligned Strategy Lab packets | Read-only result-table count; related derived tables share observations |
| 202 candidate definitions | Persisted candidate table; not a count of profitable strategies |
| Four cost scenarios | 0 / 20 / 40 / 60bp, each with 202 persisted metric rows |
| 144 Walk-Forward metric rows | Persisted evaluation records; not 144 independent trials |
| Eight curated knowledge topics | Original summaries of project research, operations and Hermes workflows |

Counts describe **one local installation at the inventory date**. They are non-additive metadata. Raw datasets, private reports, personal memory, conversations and external skill trees are not distributed. Source code availability does not establish data redistribution rights or reproduce installation-specific results.

## Review decisions

- Present data accumulation, experiment protocols, evidence linkage and operational auditability as the core strengths.
- Replace the draft's historical-return comparison with a protocol comparison. Different entry prices, holding periods, universes and costs do not support a single system return claim.
- Retain negative findings: cost drag, unstable complex models, weak absolute effects against negative baselines and non-executable label curves.
- Retain current gaps: market snapshot metadata marked stale; Forward collector absent; 21 observed Forward business dates; emotion snapshots cover one date. No data refresh, ledger append, complete PIT re-audit or new backtest was performed for this publication.
- Describe historical reports as retained local evidence. Their full data / code version correspondence and performance were not reproduced in this review. Local research archives may include material outside the published source snapshot.
- Keep the Qlib, OpenBB and LEAN comparison as architectural positioning, with the existing official source links. No parity or superiority claim is added.

## Verification and provenance

The inventory read the online main database through its existing Router owner, and used read-only connections for offline sidecars. Private inventory files and historical reports remain local; identity hashes, units, dates and limitations appear in the [aggregate evidence summary](knowledge/evidence-snapshot-2026-10-19.json). Hashes of inaccessible sources are identity references, not independent public proof of their contents.

Publication validation checks local documentation links and anchors, UTF-8 / JSON / SVG parsing, allowed file scope, conflict markers, sensitive patterns and exact local credential matches. Both vector assets are rendered for visual review. The original workspace's tracked contents, index, branch, HEAD, protected configuration / memory files and eight frozen database hashes are compared against the publication baseline.

The completed local check covered **15 changed files, 137 local links, nine anchors, one JSON document and two SVG assets**, with zero findings. Exact-value checks against 24 local credential / sensitive configuration values found no matches. Both SVG previews were visually reviewed. All seven original-workspace preservation categories matched the baseline, including eight unchanged frozen databases. These scans are bounded checks, not a guarantee that arbitrary future additions are safe to publish.

Software checks for the unchanged code are available in the [hosted Windows CI at `53022f7`](https://github.com/Shixi216/aiquant-lite/actions/runs/39104295548): **1794 backend passes, 0 failures, 4 skips, 8 deselections; 5 frontend passes and a successful production build**. These are software results for that commit. The new publication commit triggers the repository's normal backend and frontend CI; its outcome should be checked in [Actions](https://github.com/Shixi216/aiquant-lite/actions/workflows/ci.yml).

The [earlier source audit](release-audit-2026-10-18.md) records the isolated offline test mode, native Windows combined-process limitation and unverified local-data / network scope. Documentation publication does not change those conclusions or validate trading performance.

[Research evidence hub](knowledge/README.md) · [Project overview](../README.md)
