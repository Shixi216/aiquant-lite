# Publication review — 2026-10-18

This review covers the public source snapshot of AIQUANT-LITE 0.10.0. It was prepared in an isolated checkout based on the remote default branch, without publishing private local commit history, databases, credentials, logs or generated research results.

## Validation results

| Check | Result |
|---|---|
| Python offline regression | **1741 passed, 0 failed, 0 errors, 4 skipped** |
| Outside the offline selection | **8 deselected**: six prepared-local-database checks and two network checks |
| Frontend unit tests | **5 passed, 0 failed** |
| TypeScript and Vite production build | **Passed** |
| Ruff | **Passed** |
| No-live-execution boundary | **A–J passed** |
| npm dependency audit | **0 known vulnerabilities** at review time |
| Long combined Windows test process | **Native access violation (0xC0000005)**; not counted as a successful run |
| Publication credential/artifact scan | No local credential matches, recognizable token signatures, conflict markers or forbidden runtime artifacts detected |
| Original workspace preservation | Tracked file contents, original index, branch, HEAD and local `.env` retained |

Python results combine two completed disjoint module groups, 42 completed fresh module processes for the remaining group, scoped WebUI/replay/desktop checks, and ten follow-up timezone regressions. Test IDs are deduplicated with the later completed result taking precedence. All use temporary databases. The WebUI regression covers 58 cases including two cross-origin access checks; replay checks were repeated after restoring the required frozen evaluation-framework specification. The supplied `scripts.run_offline_tests` runner and CI use module isolation. The long combined-process Windows crash remains unresolved in this environment. These results do not establish that one unsegmented pytest process passes.

The [first hosted Windows CI run](https://github.com/Shixi216/aiquant-lite/actions/runs/39057934472) passed the frontend job but recorded 1672 backend passes, 28 failures, one collection error and four skips. It exposed session-timezone assumptions in completed-trading-date and fact-date queries, plus a Qt/Pydantic import-order conflict when the desktop workspace test ran independently. Follow-up changes anchor both date queries to Asia/Shanghai, reject naive cutoffs, initialize validation modules before Qt in that test, and print failure details from the offline runner. Ten new regressions cover UTC, Tokyo and Los Angeles database sessions; seven reproduced the original defects before repair, and all ten pass after repair. The four affected test modules pass all 80 cases in independent processes, including all 31 desktop workspace cases. Hosted results must be checked against the subsequent commit's Actions run.

Environment: Windows, Python 3.11.1, pytest 9.1.1, DuckDB 1.5.4 and Node.js 24.21.0. A non-blocking Starlette/httpx deprecation warning and a Vite chart-chunk size warning remain.

## Changes reviewed and repaired

- Published the Vue research workspace, read-only WebUI services, updated market/history APIs, cross-sectional scoring, research modules and associated tests.
- Rewrote Chinese and English project pages around the current implementation, with a custom vector banner, real fixture-mode screenshots, an architecture overview and primary-source comparisons with Qlib, OpenBB and LEAN.
- Replaced obsolete fixed-60/40 and historical test-count claims. Current documentation distinguishes the four-dimensional rank score, its 60/40 fallback and separate orchestration experiments.
- Preserved original timestamp representations for new DecisionPacket rows so database-session timezone conversion does not invalidate their hashes. Reads also verify agreement between payload timestamps and metadata columns. Hash algorithms and existing data files were not rewritten.
- Made historical replay's 16:00 cutoff explicitly use Asia/Shanghai; regressions cover Tokyo and UTC sessions and reject data first available after the cutoff.
- Anchored completed-trading-date and market-fact day boundaries to Asia/Shanghai instead of the database session timezone, with explicit timezone validation on the completed-date cutoff.
- Made frozen-packet GET requests return a missing-data response without creating a database, and honor the repository's configured database path.
- Defaulted the WebUI to loopback and limited CORS to the local development origins, without credentialed cross-origin access.
- Made launch and diagnostic paths portable, removed a personal administrator mapping, kept an online vision diagnostic behind its explicit entry point, and replaced diagnostic cache deletion with a disposable cache.
- Updated known-vulnerable frontend dependencies to ECharts 6.1.0 and source-map-js 1.2.2. The original advisories are [ECharts](https://github.com/advisories/GHSA-fgmj-fm8m-jvvx) and [source-map-js](https://github.com/advisories/GHSA-68fv-2mgg-jv7q).
- Repaired outdated model-routing mocks and isolated desktop workflow inputs in synthetic databases. Static label and feature specifications were retained byte-for-byte, including in Git's staged blobs.
- Corrected the desktop workspace test's Qt/Pydantic initialization order so it can be collected in a fresh process, and made offline-runner failures print bounded diagnostic details in CI logs.
- Retained a session-scoped QApplication in the test harness and verified the scoped desktop/API combination with 100 passed and three artifact-dependent skips. Long combined runs still crashed natively; module isolation is the validated execution mode and the native crash cause remains unverified.
- Added read-only GitHub Actions workflows for backend and frontend checks. Local results above do not assert that a remote Actions run has already completed.

## Explicitly unverified scope

The six `local_state` checks cover existing-data scanner routes and performance, an older fixed research baseline, and desktop database/migration/business-count acceptance. They require a separately prepared local database and `--run-local-state`; this review did not substitute empty data for those requirements. The two network checks were excluded with `-m "not network and not local_state"`.

The following artifact-dependent checks were skipped:

- `tests.test_stage12d_delivery::test_portable_artifact_contains_both_executables`: portable artifact has not been built.
- `tests.test_stage12d_delivery::test_portable_artifact_passes_package_policy`: portable artifact has not been built.
- `tests.test_stage12d_delivery::test_installer_and_checksum_manifest_exist`: installer artifact has not been built.
- `tests.test_experiment_evaluation_v1::test_51_stage10_report_contains_no_sensitive_config_key_name`: acceptance report not generated in isolated test run.

External provider and model connectivity, licensed-data completeness, existing-database migration compatibility, packaged installers and strategy profitability remain unverified. Older decision packets lacking preserved timestamp representations still require independent compatibility checks when moved between time zones.

No production database, order, trade, position or runtime service was intentionally changed by this publication workflow. The review used local fixtures and temporary databases; screenshots contain development samples.
