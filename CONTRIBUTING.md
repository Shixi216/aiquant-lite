# Contributing

Thank you for helping improve aiquant-lite.

1. Create a focused branch and keep unrelated changes out of the pull request.
2. Never commit `.env`, tokens, downloaded private documents, local databases, or logs.
3. Preserve source attribution and explicit verification status in every data contract.
4. Add or update tests for behavior changes.
5. Run `uv run pytest` and `uv run ruff check .` before opening a pull request.

Changes that introduce order execution, broker credentials, or unverifiable generated market data
will not be accepted into the default project scope.
