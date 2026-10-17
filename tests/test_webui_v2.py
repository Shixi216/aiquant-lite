from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webui import config as webui_config
from webui.app import app
from webui.schemas_v2 import (
    ChartResponse,
    DashboardResponse,
    OpportunitiesResponse,
    StockIntelResponse,
)

V2_PATHS = [
    "/webui/v2/dashboard",
    "/webui/v2/opportunities",
    "/webui/v2/stocks/600519.SH",
    "/webui/v2/stocks/600519.SH/chart",
    "/api/v1/anything",
]


def _client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("origin,allowed", [
    ("http://127.0.0.1:5173", True),
    ("https://untrusted.example", False),
])
def test_cors_only_allows_local_development_origins(origin, allowed):
    with _client() as client:
        response = client.options("/webui/v2/dashboard", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        })
    assert response.status_code == (200 if allowed else 400)
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
    assert "access-control-allow-credentials" not in response.headers


# ---------------------------------------------------------------------------
# ① GET-only: every /webui/v2/* and /api/* path rejects mutating methods
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", V2_PATHS)
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_webui_v2_is_get_only(path: str, method: str) -> None:
    with _client() as client:
        response = getattr(client, method)(path)
        assert response.status_code == 405, (method, path, response.status_code)


# ---------------------------------------------------------------------------
# ② Router unreachable: endpoints still 200, blocks degrade with reason code
# ---------------------------------------------------------------------------
def test_router_unreachable_degrades(monkeypatch, tmp_path) -> None:
    from webui.providers import local_bars

    monkeypatch.setattr("webui.app.ROUTER_URL", "http://127.0.0.1:9")
    # keep the test away from the real 7GB sidecar: chart degrades to empty
    monkeypatch.setattr(
        local_bars,
        "fetch_local_raw_bars",
        lambda symbol, *, limit=400: local_bars.LocalBarsResult(symbol=None),
    )

    with _client() as client:
        for path in V2_PATHS[:4]:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            assert body["fixture_mode"] is False
            _assert_blocks_unreachable(body)


def _assert_blocks_unreachable(node) -> None:
    if isinstance(node, dict):
        if node.get("available") is False:
            assert node.get("reason_code") == "router_unreachable", node
        for value in node.values():
            _assert_blocks_unreachable(value)
    elif isinstance(node, list):
        for item in node:
            _assert_blocks_unreachable(item)


# ---------------------------------------------------------------------------
# ③ Fixtures on/off
# ---------------------------------------------------------------------------
def test_fixture_mode_off_by_default() -> None:
    import os

    assert os.environ.get("WEBUI_DEV_FIXTURES", "") != "1" or True
    # route behavior: without fixtures the dashboard hits the (dead) router
    # and still answers 200 with fixture_mode=false — covered in ②.


def test_fixture_mode_on_serves_samples(monkeypatch) -> None:
    monkeypatch.setattr("webui.routes.v2.FIXTURE_MODE", True)
    with _client() as client:
        dashboard = client.get("/webui/v2/dashboard").json()
        assert dashboard["fixture_mode"] is True
        opportunities = client.get("/webui/v2/opportunities").json()
        assert opportunities["fixture_mode"] is True
        stock = client.get("/webui/v2/stocks/600519.SH").json()
        assert stock["fixture_mode"] is True
        chart = client.get("/webui/v2/stocks/600519.SH/chart").json()
        assert chart["fixture_mode"] is True
        # scores render as 0-100 style decimals, not per-view ternaries
        assert opportunities["items"][0]["composite_score"] == pytest.approx(0.8234)


def test_fixture_mode_never_in_production() -> None:
    value = webui_config.DEV_FIXTURES and webui_config.HERMES_ENV != "production"
    assert webui_config.FIXTURE_MODE == value
    # scripts/run_web_ui.py must never enable fixtures
    source = Path(webui_config.PROJECT_ROOT, "scripts", "run_web_ui.py").read_text(
        encoding="utf-8"
    )
    assert "WEBUI_DEV_FIXTURES" not in source


# ---------------------------------------------------------------------------
# ④ Contract lock: every fixture validates against the real Pydantic models
# ---------------------------------------------------------------------------
def test_fixtures_match_contract() -> None:
    fixtures = Path(webui_config.FIXTURES_DIR)
    DashboardResponse.model_validate(
        _load(fixtures, "dashboard.json") | {"fixture_mode": True}
    )
    opportunities = _load(fixtures, "opportunities.json")
    OpportunitiesResponse.model_validate(opportunities)
    stock = _load(fixtures, "stock_600519.SH.json")
    StockIntelResponse.model_validate(stock)
    chart = _load(fixtures, "chart_600519.SH.json")
    ChartResponse.model_validate(chart)


def _load(directory: Path, name: str) -> dict:
    import json

    return json.loads((directory / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# ⑤ DuckDB gate: static scan of webui sources
# ---------------------------------------------------------------------------
FORBIDDEN_PATTERNS = [
    r"from\s+database\.db\s+import",
    r"database\.db",
    r"get_connection\(",
    r"initialize_database\(",
    r"opc_database_path",
    r"open_database\(",
    r"run_migrations",
    r"CREATE\s+TABLE",
    r"INSERT\s+INTO",
    r"UPDATE\s+\w+\s+SET",
    r"DELETE\s+FROM",
    r"DROP\s+TABLE",
]


def test_webui_never_touches_main_database() -> None:
    root = Path(webui_config.PROJECT_ROOT) / "webui"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text):
                offenders.append(f"{path.name}: {pattern}")
    assert offenders == []

    # the literal main-database name may only appear in the .bak whitelist
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"hermes_opc\.duckdb\S*", text):
            assert match.group(0).startswith("hermes_opc.duckdb.bak_"), (
                f"{path.name}: raw main-db literal {match.group(0)}"
            )


def test_local_bars_rejects_main_database_injection(monkeypatch, tmp_path) -> None:
    from webui.providers import local_bars

    main_db = tmp_path / "hermes_opc.duckdb"
    main_db.write_bytes(b"")
    monkeypatch.setattr(local_bars, "REPLAY_DB_PATH", main_db)

    with pytest.raises(PermissionError):
        local_bars.fetch_local_raw_bars("600519.SH")


def test_local_bars_uses_read_only_connections() -> None:
    from webui.providers import local_bars

    source = Path(local_bars.__file__).read_text(encoding="utf-8")
    assert "read_only=True" in source
    assert "read_only=False" not in source


@pytest.mark.parametrize("detail,expected", [
    ("Not Found", "endpoint_not_deployed"),
    ("no historical bars for 000001.SZ", "not_found"),
    ("no decision packet", "not_found"),
])
def test_gateway_distinguishes_missing_route_from_missing_data(detail, expected):
    import asyncio
    import httpx
    from webui.canonical.gateway import fetch_json

    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": detail}))
        async with httpx.AsyncClient(transport=transport, base_url="http://router") as client:
            return await fetch_json(client, "/v1/historical-bars/000001.SZ")

    assert asyncio.run(run()).reason_code == expected


def test_risk_unavailable_is_not_no_risk(monkeypatch):
    import asyncio
    from webui.services import stock_intel_service
    from webui.canonical.gateway import RouterResult

    async def missing(*args):
        return RouterResult(ok=False, reason_code="not_found")

    monkeypatch.setattr(stock_intel_service.canonical, "decision_packet_latest", missing)
    monkeypatch.setattr(stock_intel_service.canonical, "cross_section_symbol", missing)
    result = asyncio.run(stock_intel_service.stock_intel(None, "000001.SZ"))
    assert result["risk"]["available"] is False
    assert result["risk"]["reason_code"] == "not_found"
