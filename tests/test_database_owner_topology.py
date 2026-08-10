from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from database.connection_manager import connect_database
from router.api.app import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _imports(source: str) -> set[str]:
    tree = ast.parse(source)
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def test_mcp_and_compatibility_proxy_do_not_import_database_runtime() -> None:
    mcp_source = (PROJECT_ROOT / "mcp_servers/finance_data/server.py").read_text(
        encoding="utf-8"
    )
    proxy_source = (PROJECT_ROOT / "data_hub_proxy_app.py").read_text(
        encoding="utf-8"
    )
    for source in (mcp_source, proxy_source):
        assert "duckdb.connect" not in source
        assert "get_connection(" not in source
        assert not any(name == "database.db" for name in _imports(source))


def test_stock_report_cli_has_no_direct_duckdb_open() -> None:
    source = (PROJECT_ROOT / "scripts/stock_report.py").read_text(encoding="utf-8")
    assert "duckdb.connect" not in source
    assert "/v1/research/stock-report" in source


def test_router_exposes_internal_owner_routes_without_openapi_expansion() -> None:
    client = TestClient(app)
    assert client.get("/health").json()["database_owner"]["process"] == "router"
    paths = app.openapi()["paths"]
    assert "/v1/research/stock-report" not in paths
    assert "/v1/market-facts/verify" not in paths
    assert client.post("/v1/research/stock-report", json={}).status_code == 422


def test_exception_rolls_back_closes_and_allows_next_connection(tmp_path: Path) -> None:
    database = tmp_path / "recovery.duckdb"
    try:
        with connect_database(database, configured_path=database) as connection:
            connection.execute("CREATE TABLE sample(value INTEGER)")
            connection.execute("BEGIN TRANSACTION")
            connection.execute("INSERT INTO sample VALUES (1)")
            raise RuntimeError("injected failure")
    except RuntimeError as exc:
        assert str(exc) == "injected failure"

    with connect_database(database, configured_path=database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone() == (0,)

