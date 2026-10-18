# Project presentation design — 2026-10-19

The project introduction uses a product-first sequence: brand and navigation, capabilities, actual UI previews, a dated research inventory, workflows, reference architectures, deployment and validation.

## References and original work

- [Qlib's README](https://github.com/microsoft/qlib) demonstrates prominent research positioning, a framework overview and a path from capabilities to setup.
- [OpenBB's README](https://github.com/openbq-org/OpenBB) leads with data-platform positioning and connects interfaces to concrete usage.
- [OpenHands' README](https://github.com/OpenHands/OpenHands) provides a concise product introduction, navigation and scannable capabilities.

These references informed layout and information hierarchy. Copy, graphics and icons in this update are original. No external logos, screenshots, popularity counts, performance figures or endorsement claims are imported. The Qlib / OpenBB / LEAN table compares design directions, not established parity.

## Visual assets

| Asset | Purpose |
|---|---|
| [Hero](assets/hero.svg) | Brand positioning and a conceptual research loop |
| [Capability map](assets/capability-map.svg) | Six connected research and engineering capabilities |
| [Research evidence](assets/research-evidence.svg) | Dated aggregate inventory with explicit counting units |
| [Research workflow](assets/research-workflow.svg) | Conceptual stages and knowledge curation |
| [Architecture](assets/architecture-overview.svg) | Research surfaces, core, model configuration and database ownership |

All diagrams are native SVG with accessible titles / descriptions, no remote dependencies, scripts or external imagery. SVG counts refer to the [dated inventory](knowledge/evidence-snapshot-2026-10-19.json), not a live feed. The existing PNG product previews retain their development-fixture captions.

Research routes have separate contracts. The architecture map is conceptual; WebUI can read approved sidecars directly. Hermes decision evaluation does not itself create a DecisionPacket. Curated knowledge is a documented review practice, not a claim of autonomous learning.

This update changes project documentation and vector assets. The original workspace, services, credentials, databases and frozen research artifacts are preserved. See the [research evidence hub](knowledge/README.md) for result boundaries and the [source audit](release-audit-2026-10-18.md) for software-validation limitations.

## Presentation verification

Both README files were rendered through GitHub's Markdown API and previewed locally at desktop (1160 × 1000) and mobile (390 × 844) viewport sizes. All 20 local image instances loaded, with no page-width overflow in either language. Badge placeholders were used only in the local preview; repository badges retain their real URLs.

The publication check passed for seven changed files, 114 local documentation links, eight anchors and four parsed / rendered SVG assets. It found no conflict markers, sensitive patterns or exact matches against 24 local sensitive configuration values. The original-workspace preservation categories matched the baseline, including eight frozen database hashes. These are bounded presentation and preservation checks; they do not replace software or research validation.
