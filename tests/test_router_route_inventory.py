"""Prove the five new GET endpoints are mounted without starting a server."""
from __future__ import annotations

EXPECTED_NEW_PATHS = [
    "/v1/historical-bars/{symbol}",
    "/v1/cross-section/latest",
    "/v1/cross-section/{symbol}",
    "/v1/decision-packets/v2/{symbol}/latest",
    "/v1/scanner/runs",
    "/v1/market-snapshots/latest/breadth",
]


def test_new_get_paths_registered() -> None:
    from router.api.app import app

    paths = app.openapi()["paths"]
    missing = [path for path in EXPECTED_NEW_PATHS if path not in paths]
    assert missing == []
    for path in EXPECTED_NEW_PATHS:
        methods = set(paths[path].keys())
        assert methods == {"get"}, (path, methods)
