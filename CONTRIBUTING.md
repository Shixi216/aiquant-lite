# Contributing

Thank you for helping improve aiquant-lite.

1. Create a focused branch and keep unrelated changes out of the pull request.
2. Never commit `.env`, tokens, downloaded private documents, local databases, or logs.
3. Preserve source attribution and explicit verification status in every data contract.
4. Add or update tests for behavior changes.
5. Run `uv run python -m scripts.run_offline_tests --workers 2`, `uv run ruff check .`,
   and `uv run python -m scripts.check_no_live_execution` in an isolated environment.
6. For frontend changes, run `npm --prefix frontend test` and `npm --prefix frontend run build`.

Checks marked `local_state` require independently prepared local research data and explicit
`--run-local-state` opt-in. Use a copy of the database and stop its online owners before direct
database acceptance checks. Network and local-state checks are outside the offline CI scope.

Changes that introduce order execution, broker credentials, or unverifiable generated market data
will not be accepted into the default project scope.
