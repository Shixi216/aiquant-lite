from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PRODUCTION_DIRECTORIES = (
    "router",
    "trading",
    "manual_tracking",
    "config",
    "mcp_servers",
    "data_hub",
)

BROKER_EXECUTION_DEPENDENCIES = {
    "alpaca-trade-api",
    "easytrader",
    "futu-api",
    "ib-insync",
    "pyautogui",
    "tigeropen",
    "vnpy",
    "xtquant",
}
BROKER_OR_CLIENT_IMPORTS = {
    "easytrader",
    "futu",
    "ib_insync",
    "pyautogui",
    "tigeropen",
    "vnpy",
    "win32api",
    "win32com",
    "win32con",
    "win32gui",
    "xtquant",
}
BROWSER_IMPORTS = {"playwright", "selenium"}
CLIPBOARD_IMPORTS = {"pyperclip", "win32clipboard"}
BANNED_CONFIG_NAMES = {
    "BROKER_ACCOUNT",
    "BROKER_PASSWORD",
    "TRADING_PASSWORD",
    "QMT_PATH",
    "XTQUANT_TOKEN",
    "LIVE_TRADING_ENABLED",
}
ALLOWED_FINANCE_MCP_TOOLS = {
    "create_explicit_decision",
    "get_experiment_report",
    "get_market_snapshot",
    "get_realtime_quote",
    "get_stock_basic",
    "get_daily_bars",
    "get_financial_statement",
    "list_announcements",
    "search_finance_news",
    "verify_market_fact",
    "parse_market_scanner_query",
    "research_candidates",
    "scan_a_share_market",
}
OPENAPI_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}

# Captured from the actual FastAPI app immediately before the 0.9.0 stage-1 refactor.
STAGE0_ROUTE_METHODS = {
    ("GET", "/"),
    ("GET", "/health"),
    ("GET", "/v1/audit/tasks"),
    ("GET", "/v1/audit/tasks/{task_id}"),
    ("POST", "/v1/invoke"),
    ("POST", "/v1/pipelines/announcement-verification"),
    ("POST", "/v1/pipelines/news-analysis"),
    ("GET", "/v1/roles"),
    ("GET", "/v1/roles/{role_name}"),
    ("GET", "/v1/routing/policy"),
    ("POST", "/v1/trading/backtests"),
    ("GET", "/v1/trading/brokers"),
    ("POST", "/v1/trading/decisions"),
    ("POST", "/v1/trading/decisions/from-data"),
    ("POST", "/v1/trading/live/orders"),
    ("GET", "/v1/trading/paper/account"),
    ("POST", "/v1/trading/paper/kill-switch"),
    ("POST", "/v1/trading/paper/orders"),
    ("POST", "/v1/trading/paper/protective-exits"),
    ("POST", "/v1/trading/portfolio/optimize"),
    ("GET", "/v1/trading/reviews/daily/{review_date}"),
    ("GET", "/v1/trading/reviews/daily/{review_date}/markdown"),
}
EXPECTED_STAGE1_REMOVALS = {
    ("GET", "/v1/trading/brokers"),
    ("POST", "/v1/trading/live/orders"),
}
STAGE1_ROUTE_METHODS = STAGE0_ROUTE_METHODS - EXPECTED_STAGE1_REMOVALS
STAGE2_CANONICAL_ROUTE_METHODS = {
    ("POST", "/v1/decisions/from-data"),
    ("POST", "/v1/simulations/backtests"),
    ("POST", "/v1/simulations/portfolio/optimize"),
    ("POST", "/v1/simulations/paper-orders"),
    ("GET", "/v1/simulations/paper-account"),
    ("POST", "/v1/simulations/protective-exits"),
    ("POST", "/v1/simulations/kill-switch"),
    ("POST", "/v1/reviews/daily"),
    ("GET", "/v1/reviews/daily/{date}"),
    ("GET", "/v1/reviews/daily/{date}/markdown"),
}
STAGE2_DEPRECATED_ROUTE_METHODS = {
    route
    for route in STAGE1_ROUTE_METHODS
    if route[1].startswith("/v1/trading/")
}
STAGE2_ROUTE_METHODS = STAGE1_ROUTE_METHODS | STAGE2_CANONICAL_ROUTE_METHODS
STAGE3_DECISION_ROUTE_METHODS = {
    ("GET", "/v1/decisions/{decision_id}"),
    ("GET", "/v1/decisions/{decision_id}/versions"),
    ("GET", "/v1/decisions/{decision_id}/versions/{version}"),
    ("POST", "/v1/decisions/{decision_id}/challenge"),
}
STAGE3_CANONICAL_ROUTE_METHODS = (
    STAGE2_CANONICAL_ROUTE_METHODS | STAGE3_DECISION_ROUTE_METHODS
)
STAGE3_ROUTE_METHODS = STAGE2_ROUTE_METHODS | STAGE3_DECISION_ROUTE_METHODS
STAGE4_MANUAL_ROUTE_METHODS = {
    ("POST", "/v1/manual-trades/previews"),
    ("GET", "/v1/manual-trades/previews/{confirmation_id}"),
    ("POST", "/v1/manual-trades/previews/{confirmation_id}/confirm"),
    ("POST", "/v1/manual-trades/previews/{confirmation_id}/cancel"),
    ("GET", "/v1/manual-trades"),
    ("GET", "/v1/manual-trades/{trade_id}"),
    ("POST", "/v1/manual-trades/{trade_id}/corrections"),
    ("GET", "/v1/manual-positions"),
    ("GET", "/v1/manual-positions/{position_id}"),
}
STAGE4_CANONICAL_ROUTE_METHODS = (
    STAGE3_CANONICAL_ROUTE_METHODS | STAGE4_MANUAL_ROUTE_METHODS
)
STAGE4_ROUTE_METHODS = STAGE3_ROUTE_METHODS | STAGE4_MANUAL_ROUTE_METHODS
STAGE5_RISK_REVIEW_ROUTE_METHODS = {
    ("POST", "/v1/manual-positions/{position_id}/risk-reviews"),
    ("GET", "/v1/manual-positions/{position_id}/risk-reviews"),
    ("GET", "/v1/manual-positions/{position_id}/risk-reviews/latest"),
}
STAGE5_CANONICAL_ROUTE_METHODS = (
    STAGE4_CANONICAL_ROUTE_METHODS | STAGE5_RISK_REVIEW_ROUTE_METHODS
)
STAGE5_ROUTE_METHODS = STAGE4_ROUTE_METHODS | STAGE5_RISK_REVIEW_ROUTE_METHODS
V0100_SENTIMENT_ROUTE_METHODS = {
    ("POST", "/v1/sentiment/analyze"),
    ("GET", "/v1/sentiment/symbols/{symbol}"),
    ("GET", "/v1/sentiment/market"),
    ("GET", "/v1/sentiment/events/{event_cluster_id}"),
    ("POST", "/v1/sentiment/evaluate"),
}
V0100_POLICY_NEWS_ROUTE_METHODS = {
    ("POST", "/v1/policy-news/analyze"),
    ("GET", "/v1/policy-news/symbols/{symbol}"),
    ("GET", "/v1/policy-news/sectors/{sector}"),
    ("GET", "/v1/policy-news/events/{event_cluster_id}"),
    ("POST", "/v1/policy-news/evaluate"),
}
V0100_CAPITAL_FLOW_ROUTE_METHODS = {
    ("POST", "/v1/capital-flow/analyze"),
    ("GET", "/v1/capital-flow/symbols/{symbol}"),
    ("GET", "/v1/capital-flow/sectors/{sector}"),
    ("GET", "/v1/capital-flow/market"),
    ("POST", "/v1/capital-flow/evaluate"),
}
V0106_FULL_MARKET_ROUTE_METHODS = {
    ("POST", "/v1/universe/sync"),
    ("GET", "/v1/universe"),
    ("GET", "/v1/universe/{symbol}"),
    ("POST", "/v1/market-snapshots/sync"),
    ("GET", "/v1/market-snapshots/latest"),
    ("POST", "/v1/data-expansion/run"),
    ("GET", "/v1/data-coverage/latest"),
    ("POST", "/v1/candidates/enrich"),
}
V0109_ORCHESTRATION_ROUTE_METHODS = {
    ("POST", "/v1/orchestration/screen"),
    ("POST", "/v1/orchestration/research"),
    ("POST", "/v1/orchestration/decision-shadow"),
    ("GET", "/v1/orchestration/symbols/{symbol}"),
    ("POST", "/v1/orchestration/evaluate"),
}
V0110_SCANNER_ROUTE_METHODS = {
    ("POST", "/v1/scanner/parse"),
    ("POST", "/v1/scanner/scan"),
    ("GET", "/v1/scanner/runs/{run_id}"),
    ("GET", "/v1/scanner/runs/{run_id}/candidates"),
    ("GET", "/v1/scanner/symbols/{symbol}"),
    ("POST", "/v1/scanner/evaluate"),
}
V0111_EXPERIMENT_ROUTE_METHODS = {
    ("POST", "/v1/experiments"),
    ("GET", "/v1/experiments/{experiment_id}"),
    ("POST", "/v1/experiments/{experiment_id}/run"),
    ("GET", "/v1/experiment-runs/{run_id}"),
    ("POST", "/v1/evaluations/forward-returns/update"),
    ("GET", "/v1/experiment-runs/{run_id}/metrics"),
    ("GET", "/v1/experiment-runs/{run_id}/observations"),
    ("GET", "/v1/experiment-runs/{run_id}/report"),
    ("POST", "/v1/backtests/run"),
}
STAGE11_INTEGRATION_ROUTE_METHODS = {
    ("GET", "/v1/integration/tools"),
    ("POST", "/v1/integration/workflow/parse"),
    ("POST", "/v1/integration/workflow/scan"),
    ("POST", "/v1/integration/workflow/research"),
    ("POST", "/v1/integration/workflow/decision"),
    ("POST", "/v1/integration/wecom/simulate"),
    ("GET", "/v1/integration/system/health"),
    ("GET", "/v1/integration/system/doctor"),
    ("GET", "/v1/integration/openapi-summary"),
    ("GET", "/v1/integration/experiments/{run_id}"),
}
CURRENT_ROUTE_METHODS = (
    STAGE5_ROUTE_METHODS
    | V0100_SENTIMENT_ROUTE_METHODS
    | V0100_POLICY_NEWS_ROUTE_METHODS
    | V0100_CAPITAL_FLOW_ROUTE_METHODS
    | V0106_FULL_MARKET_ROUTE_METHODS
    | V0109_ORCHESTRATION_ROUTE_METHODS
    | V0110_SCANNER_ROUTE_METHODS
    | V0111_EXPERIMENT_ROUTE_METHODS
    | STAGE11_INTEGRATION_ROUTE_METHODS
)
STAGE5_DATABASE_SCHEMA_HASH = (
    "1e5ce68d74bd0ddd0b1ca947da59d6d258f91dc33f96856b9b6d21c39c546c11"
)
STAGE6_REQUIRED_PACKAGES = {
    "trading/research/technical",
    "trading/research/fundamental",
    "trading/research/sentiment",
    "trading/research/policy_news",
    "trading/research/events",
    "trading/decision_support/orchestrator",
    "trading/decision_support/risk_rules",
    "trading/decision_support/adversarial_review",
    "trading/decision_support/decision_packets",
    "trading/simulation/backtest",
    "trading/simulation/paper_account",
    "trading/simulation/portfolio",
    "trading/simulation/hypothetical_orders",
    "trading/review/daily",
    "trading/review/attribution",
    "trading/review/rule_evaluation",
    "manual_tracking/trades",
    "manual_tracking/positions",
    "manual_tracking/confirmations",
    "manual_tracking/risk_reviews",
}
STAGE6_LEGACY_MODULES = {
    "trading.analytics",
    "trading.backtest",
    "trading.decision_repository",
    "trading.decision_service",
    "trading.manual_position_risk_repository",
    "trading.manual_position_risk_service",
    "trading.manual_trade_chat",
    "trading.manual_trade_repository",
    "trading.manual_trade_service",
    "trading.paper",
    "trading.persistence",
    "trading.portfolio",
    "trading.position_research_data",
    "trading.read_repositories",
    "trading.replay",
    "trading.review_service",
    "trading.risk",
    "trading.service",
}

EXTERNAL_EXECUTION_CONTEXT = re.compile(
    r"broker|qmt|xtquant|citic|中信|券商|交易客户端|trading[\s_-]*(?:client|portal|page)",
    re.IGNORECASE,
)
BROWSER_ACTION = re.compile(
    r"\.(?:click|dblclick|fill|goto|press|tap|type)\s*\(",
    re.IGNORECASE,
)
SUBPROCESS_CALL = re.compile(
    r"(?:subprocess\.(?:call|check_call|check_output|popen|run)|os\.system)\s*\(",
    re.IGNORECASE,
)
SOCKET_USE = re.compile(
    r"(?:import\s+socket|from\s+socket\s+import|socket\.(?:socket|create_connection))",
    re.IGNORECASE,
)


def production_python_files() -> list[Path]:
    files: list[Path] = []
    for directory in PRODUCTION_DIRECTORIES:
        files.extend((PROJECT_ROOT / directory).rglob("*.py"))
    return sorted(files)


def _display(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _normalized_dependency_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
    if match is None:
        return ""
    return match.group(1).lower().replace("_", "-").replace(".", "-")


def _dependency_strings(document: dict[str, Any]) -> Iterable[str]:
    project = document.get("project", {})
    yield from project.get("dependencies", [])
    for dependencies in project.get("optional-dependencies", {}).values():
        yield from dependencies
    for dependencies in document.get("dependency-groups", {}).values():
        yield from dependencies


def dependency_issues() -> list[str]:
    issues: list[str] = []
    pyproject_path = PROJECT_ROOT / "pyproject.toml"
    lock_path = PROJECT_ROOT / "uv.lock"

    try:
        with pyproject_path.open("rb") as handle:
            pyproject = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"cannot parse pyproject.toml: {exc}"]

    declared = {
        _normalized_dependency_name(item)
        for item in _dependency_strings(pyproject)
    }
    forbidden_declared = sorted(declared & BROKER_EXECUTION_DEPENDENCIES)
    if forbidden_declared:
        issues.append(
            "pyproject.toml declares forbidden execution dependencies: "
            + ", ".join(forbidden_declared)
        )

    try:
        with lock_path.open("rb") as handle:
            lock_document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        issues.append(f"cannot parse uv.lock: {exc}")
        return issues

    locked = {
        _normalized_dependency_name(str(package.get("name", "")))
        for package in lock_document.get("package", [])
    }
    forbidden_locked = sorted(locked & BROKER_EXECUTION_DEPENDENCIES)
    if forbidden_locked:
        issues.append(
            "uv.lock contains forbidden execution dependencies: "
            + ", ".join(forbidden_locked)
        )
    return issues


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0].lower() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0].lower())
    return roots


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dynamic_import_names(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in {"__import__", "import_module"}:
            continue
        module_name = _literal_string(node.args[0])
        if module_name:
            found.append((node.lineno, module_name))
    return found


def _parse_production_file(path: Path) -> tuple[ast.AST | None, str, list[str]]:
    try:
        source = path.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(path)), source, []
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return None, "", [f"{_display(path)} cannot be scanned: {exc}"]


def broker_import_issues() -> list[str]:
    issues: list[str] = []
    for path in production_python_files():
        tree, source, parse_issues = _parse_production_file(path)
        issues.extend(parse_issues)
        if tree is None:
            continue
        imports = _import_roots(tree)
        forbidden = sorted(imports & BROKER_OR_CLIENT_IMPORTS)
        if forbidden:
            issues.append(
                f"{_display(path)} imports forbidden broker/client modules: "
                + ", ".join(forbidden)
            )
        if imports & BROWSER_IMPORTS and EXTERNAL_EXECUTION_CONTEXT.search(source):
            issues.append(
                f"{_display(path)} imports browser automation in an external execution context"
            )
        for line, module_name in _dynamic_import_names(tree):
            root = module_name.split(".", 1)[0].lower()
            if root in BROKER_OR_CLIENT_IMPORTS:
                issues.append(
                    f"{_display(path)}:{line} dynamically imports forbidden module {module_name}"
                )
    return issues


def config_issues() -> list[str]:
    issues: list[str] = []
    paths = [*production_python_files(), PROJECT_ROOT / ".env.example"]
    credential_pattern = re.compile(
        r"\b(?:BROKER_[A-Z0-9_]*(?:ACCOUNT|AUTH|COOKIE|PASSWORD|SECRET|TOKEN)"
        r"|(?:AUTH|COOKIE|PASSWORD|SECRET|TOKEN)[A-Z0-9_]*_BROKER)\b",
        re.IGNORECASE,
    )
    for path in paths:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"{_display(path)} cannot be scanned: {exc}")
            continue
        upper_source = source.upper()
        found = {
            name
            for name in BANNED_CONFIG_NAMES
            if re.search(rf"\b(?:OPC_)?{re.escape(name)}\b", upper_source)
        }
        found.update(match.group(0).upper() for match in credential_pattern.finditer(source))
        if found:
            issues.append(
                f"{_display(path)} accepts or references forbidden settings: "
                + ", ".join(sorted(found))
            )
    return issues


def load_openapi_schema(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    from router.api.app import app

    return app.openapi()


def route_methods(schema: dict[str, Any]) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for path, path_item in schema.get("paths", {}).items():
        for method in path_item:
            if method.lower() in OPENAPI_METHODS:
                routes.add((method.upper(), path))
    return routes


def openapi_issues(schema: dict[str, Any] | None = None) -> list[str]:
    if schema is None:
        schema = load_openapi_schema()
    issues: list[str] = []
    forbidden_segment = re.compile(
        r"(?:^|/)(?:live|brokers?|broker-login|account-sync)(?:/|$)",
        re.IGNORECASE,
    )
    real_order = re.compile(r"(?:real|live)[-_/ ]?orders?", re.IGNORECASE)
    for method, path in sorted(route_methods(schema)):
        if forbidden_segment.search(path) or real_order.search(path):
            issues.append(f"OpenAPI exposes forbidden route: {method} {path}")
    return issues


def finance_mcp_tool_names() -> set[str]:
    from mcp_servers.finance_data.server import mcp

    return {tool.name for tool in mcp._tool_manager.list_tools()}


def finance_mcp_issues() -> list[str]:
    names = finance_mcp_tool_names()
    if names == ALLOWED_FINANCE_MCP_TOOLS:
        return []
    added = sorted(names - ALLOWED_FINANCE_MCP_TOOLS)
    missing = sorted(ALLOWED_FINANCE_MCP_TOOLS - names)
    issues: list[str] = []
    if added:
        issues.append("Finance Data MCP exposes unapproved tools: " + ", ".join(added))
    if missing:
        issues.append("Finance Data MCP is missing approved tools: " + ", ".join(missing))
    return issues


def indirect_execution_issues() -> list[str]:
    issues: list[str] = []
    for path in production_python_files():
        tree, source, parse_issues = _parse_production_file(path)
        issues.extend(parse_issues)
        if tree is None:
            continue
        imports = _import_roots(tree)
        context = bool(EXTERNAL_EXECUTION_CONTEXT.search(source))
        if imports & CLIPBOARD_IMPORTS and context:
            issues.append(f"{_display(path)} uses clipboard access for external execution")
        if imports & BROWSER_IMPORTS and context and BROWSER_ACTION.search(source):
            issues.append(f"{_display(path)} automates an external trading page")
        if context and SUBPROCESS_CALL.search(source):
            issues.append(f"{_display(path)} starts an external trading client")
        if context and "start-process" in source.lower():
            issues.append(f"{_display(path)} contains Start-Process for an external trading client")
        if context and SOCKET_USE.search(source):
            issues.append(f"{_display(path)} uses a local socket for an external trading client")
    return issues


def decision_to_real_order_issues() -> list[str]:
    issues: list[str] = []
    routes_path = PROJECT_ROOT / "trading" / "routes.py"
    tree, _, parse_issues = _parse_production_file(routes_path)
    issues.extend(parse_issues)
    if tree is None:
        return issues

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "decision" not in node.name:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in {
                "submit_order",
                "execute_protective_exits",
                "simulation_service",
            }:
                issues.append(
                    f"trading/routes.py:{child.lineno} decision endpoint invokes execution"
                )
            if isinstance(child, ast.Name) and child.id in {
                "paper_trading",
                "simulation_service",
            }:
                issues.append(
                    f"trading/routes.py:{child.lineno} decision endpoint reaches Paper Trading"
                )

    for path in production_python_files():
        tree, _, parse_issues = _parse_production_file(path)
        issues.extend(parse_issues)
        if tree is None:
            continue
        allowed_service_lines: set[int] = set()
        if _display(path) == "trading/simulation/service.py":
            for class_node in tree.body:
                if not isinstance(class_node, ast.ClassDef):
                    continue
                if class_node.name != "SimulationService":
                    continue
                allowed_service_lines.update(
                    child.lineno
                    for child in ast.walk(class_node)
                    if isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "submit_order"
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "submit_order":
                continue
            relative = _display(path)
            allowed = (
                relative == "trading/simulation/paper_account/engine.py"
                or (
                    relative == "trading/simulation/service.py"
                    and node.lineno in allowed_service_lines
                )
            )
            if not allowed:
                issues.append(
                    f"{relative}:{node.lineno} adds an unexpected decision-to-order path"
                )
    return issues


def manual_trade_boundary_issues() -> list[str]:
    issues: list[str] = []
    forbidden_service_paths = (
        PROJECT_ROOT
        / "trading"
        / "decision_support"
        / "orchestrator"
        / "service.py",
        PROJECT_ROOT / "trading" / "simulation" / "service.py",
    )
    for path in forbidden_service_paths:
        try:
            source = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"{_display(path)} cannot be scanned: {exc}")
            continue
        if "manualtraderepository" in source or "manual_trade_repository" in source:
            issues.append(f"{_display(path)} depends on ManualTradeRepository")
        if "confirm_preview" in source:
            issues.append(f"{_display(path)} can confirm a manual trade preview")

    scheduled_paths = (
        PROJECT_ROOT / "scripts" / "generate_daily_review.py",
        PROJECT_ROOT / "scripts" / "configure_hermes_integration.py",
    )
    for path in scheduled_paths:
        try:
            source = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"{_display(path)} cannot be scanned: {exc}")
            continue
        if "confirm_preview" in source or "/manual-trades/previews/" in source:
            issues.append(f"{_display(path)} can confirm a manual trade preview")

    for path in (PROJECT_ROOT / "router" / "services").glob("*risk*.py"):
        try:
            source = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"{_display(path)} cannot be scanned: {exc}")
            continue
        if "confirm_preview" in source or "/manual-trades/previews/" in source:
            issues.append(f"{_display(path)} can confirm a manual trade preview")

    schema = load_openapi_schema()
    allowed_position_writes = {
        ("POST", "/v1/manual-positions/{position_id}/risk-reviews"),
    }
    for method, path in route_methods(schema):
        if (
            path.startswith("/v1/manual-positions")
            and method != "GET"
            and (method, path) not in allowed_position_writes
        ):
            issues.append(f"manual position projection exposes a write route: {method} {path}")
    return issues


def stage5_review_boundary_issues(
    schema: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    from trading.schemas import (
        ManualPositionRiskReview,
        ManualRiskRecommendedAction,
    )

    allowed_actions = {
        "CONTINUE_OBSERVATION",
        "HUMAN_REVIEW_REQUIRED",
        "CONSIDER_REDUCING",
        "CONSIDER_EXITING",
    }
    actual_actions = {item.value for item in ManualRiskRecommendedAction}
    if actual_actions != allowed_actions:
        issues.append(
            "manual-position risk actions differ from the advisory allowlist: "
            + ", ".join(sorted(actual_actions))
        )

    contract_schema = ManualPositionRiskReview.model_json_schema()
    schema_text = json.dumps(contract_schema, ensure_ascii=False).lower()
    prohibited_contract_tokens = {
        "close_position",
        "submit_order",
        "place_order",
        "auto_reduce",
        "orderintent",
        "orderresult",
    }
    for token in sorted(prohibited_contract_tokens):
        if token in schema_text:
            issues.append(f"manual-position risk schema contains {token}")

    risk_service_path = (
        PROJECT_ROOT / "manual_tracking" / "risk_reviews" / "service.py"
    )
    risk_source = risk_service_path.read_text(encoding="utf-8").lower()
    for token in sorted(prohibited_contract_tokens):
        if token in risk_source:
            issues.append(
                "manual-position risk service contains an execution path token: "
                f"{token}"
            )

    signature = ast.parse(
        (
            PROJECT_ROOT
            / "trading"
            / "review"
            / "daily"
            / "service.py"
        ).read_text(
            encoding="utf-8"
        )
    )
    review_class = next(
        (
            node
            for node in signature.body
            if isinstance(node, ast.ClassDef) and node.name == "ReviewService"
        ),
        None,
    )
    if review_class is None:
        issues.append("ReviewService class is missing")
    else:
        init = next(
            (
                node
                for node in review_class.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            ),
            None,
        )
        expected_parameters = {
            "self",
            "decisions",
            "simulations",
            "manual_trades",
            "risk_reviews",
        }
        if init is None:
            issues.append("ReviewService constructor is missing")
        else:
            actual_parameters = {
                argument.arg
                for argument in [*init.args.args, *init.args.kwonlyargs]
            }
            if actual_parameters != expected_parameters:
                issues.append(
                    "ReviewService does not have exactly four read repository "
                    "capabilities"
                )
        forbidden_calls = {
            "append",
            "confirm_preview",
            "create_preview",
            "insert",
            "record_order",
            "record_paper_account",
            "submit_paper_order",
            "update",
        }
        for node in ast.walk(review_class):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "self"
            ):
                issues.append(
                    f"ReviewService invokes write method {node.func.attr}"
                )

    current_routes = route_methods(schema or load_openapi_schema())
    missing_routes = STAGE5_RISK_REVIEW_ROUTE_METHODS - current_routes
    unexpected_position_writes = {
        (method, path)
        for method, path in current_routes
        if path.startswith("/v1/manual-positions")
        and method not in {"GET"}
        and (method, path) not in STAGE5_RISK_REVIEW_ROUTE_METHODS
    }
    if missing_routes:
        issues.append(
            "missing Stage 5 risk-review routes: "
            + ", ".join(f"{method} {path}" for method, path in sorted(missing_routes))
        )
    if unexpected_position_writes:
        issues.append(
            "unexpected manual-position write routes: "
            + ", ".join(
                f"{method} {path}"
                for method, path in sorted(unexpected_position_writes)
            )
        )
    return issues


def stage6_package_structure_issues(
    schema: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    for relative in sorted(STAGE6_REQUIRED_PACKAGES):
        path = PROJECT_ROOT / Path(relative)
        if not path.is_dir():
            issues.append(f"required Stage 6 package is missing: {relative}")
        elif not (path / "__init__.py").is_file():
            issues.append(f"Stage 6 package has no __init__.py: {relative}")

    for module in sorted(STAGE6_LEGACY_MODULES):
        relative = Path(*module.split(".")).with_suffix(".py")
        if (PROJECT_ROOT / relative).exists():
            issues.append(f"legacy root module still exists: {module}")

    for path in production_python_files():
        tree, _, parse_issues = _parse_production_file(path)
        issues.extend(parse_issues)
        if tree is None:
            continue
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            for module in imported:
                if any(
                    module == legacy or module.startswith(f"{legacy}.")
                    for legacy in STAGE6_LEGACY_MODULES
                ):
                    issues.append(
                        f"{_display(path)}:{node.lineno} imports legacy module {module}"
                    )

    actual_routes = route_methods(schema or load_openapi_schema())
    if actual_routes != CURRENT_ROUTE_METHODS:
        missing = CURRENT_ROUTE_METHODS - actual_routes
        added = actual_routes - CURRENT_ROUTE_METHODS
        if missing:
            issues.append(
                "Stage 6 removed HTTP routes: "
                + ", ".join(
                    f"{method} {path}" for method, path in sorted(missing)
                )
            )
        if added:
            issues.append(
                "Stage 6 added HTTP routes: "
                + ", ".join(
                    f"{method} {path}" for method, path in sorted(added)
                )
            )

    from database.db import SCHEMA_STATEMENTS

    normalized_schema = "\n".join(
        " ".join(statement.split()) for statement in SCHEMA_STATEMENTS
    )
    actual_schema_hash = hashlib.sha256(
        normalized_schema.encode("utf-8")
    ).hexdigest()
    if actual_schema_hash != STAGE5_DATABASE_SCHEMA_HASH:
        issues.append(
            "database schema statements changed during Stage 6: "
            f"{actual_schema_hash}"
        )
    return issues


def _serialize_routes(routes: set[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"method": method, "path": path}
        for method, path in sorted(routes, key=lambda item: (item[1], item[0]))
    ]


def migration_route_diff(schema: dict[str, Any]) -> dict[str, Any]:
    current = route_methods(schema)
    removed = STAGE0_ROUTE_METHODS - current
    added = current - STAGE0_ROUTE_METHODS
    retained = current & STAGE0_ROUTE_METHODS

    return {
        "baseline": "FastAPI app inspected before Router 0.9.0 stage-1 refactor",
        "expected_removed": _serialize_routes(EXPECTED_STAGE1_REMOVALS),
        "removed": _serialize_routes(removed),
        "unexpected_removed": _serialize_routes(removed - EXPECTED_STAGE1_REMOVALS),
        "unexpected_retained": _serialize_routes(EXPECTED_STAGE1_REMOVALS & current),
        "added": _serialize_routes(added),
        "retained": _serialize_routes(retained),
        "only_expected_routes_removed": (
            removed == EXPECTED_STAGE1_REMOVALS and not added
        ),
    }


def stage2_route_diff(schema: dict[str, Any]) -> dict[str, Any]:
    current = route_methods(schema)
    removed = STAGE1_ROUTE_METHODS - current
    added = current - STAGE1_ROUTE_METHODS
    retained = current & STAGE1_ROUTE_METHODS
    return {
        "baseline": "Router 0.9.0 stage-1 OpenAPI",
        "expected_added": _serialize_routes(STAGE2_CANONICAL_ROUTE_METHODS),
        "added": _serialize_routes(added),
        "unexpected_added": _serialize_routes(added - STAGE2_CANONICAL_ROUTE_METHODS),
        "missing_new_routes": _serialize_routes(STAGE2_CANONICAL_ROUTE_METHODS - current),
        "removed": _serialize_routes(removed),
        "retained_compatibility_routes": _serialize_routes(
            STAGE2_DEPRECATED_ROUTE_METHODS & current
        ),
        "retained": _serialize_routes(retained),
        "only_expected_routes_changed": (
            added == STAGE2_CANONICAL_ROUTE_METHODS and not removed
        ),
    }


def stage5_route_diff(schema: dict[str, Any]) -> dict[str, Any]:
    current = route_methods(schema)
    removed = STAGE4_ROUTE_METHODS - current
    added = current - STAGE4_ROUTE_METHODS
    retained = current & STAGE4_ROUTE_METHODS
    return {
        "baseline": "Router 0.9.0 stage-4 OpenAPI",
        "expected_added": _serialize_routes(STAGE5_RISK_REVIEW_ROUTE_METHODS),
        "added": _serialize_routes(added),
        "unexpected_added": _serialize_routes(
            added - STAGE5_RISK_REVIEW_ROUTE_METHODS
        ),
        "missing_new_routes": _serialize_routes(
            STAGE5_RISK_REVIEW_ROUTE_METHODS - current
        ),
        "removed": _serialize_routes(removed),
        "retained": _serialize_routes(retained),
        "only_expected_routes_changed": (
            added == STAGE5_RISK_REVIEW_ROUTE_METHODS and not removed
        ),
    }


def stage6_route_diff(schema: dict[str, Any]) -> dict[str, Any]:
    current = route_methods(schema)
    removed = STAGE5_ROUTE_METHODS - current
    added = current - STAGE5_ROUTE_METHODS
    return {
        "baseline": "Router 0.9.0 stage-5 OpenAPI",
        "expected_added": [],
        "expected_removed": [],
        "added": _serialize_routes(added),
        "removed": _serialize_routes(removed),
        "retained": _serialize_routes(current & STAGE5_ROUTE_METHODS),
        "only_expected_routes_changed": not added and not removed,
    }


def run_checks(schema: dict[str, Any] | None = None) -> list[tuple[str, list[str]]]:
    if schema is None:
        schema = load_openapi_schema()
    return [
        ("A dependency check", dependency_issues()),
        ("B Python AST import check", broker_import_issues()),
        ("C configuration check", config_issues()),
        ("D OpenAPI check", openapi_issues(schema)),
        ("E Finance Data MCP check", finance_mcp_issues()),
        ("F indirect execution check", indirect_execution_issues()),
        ("G decision-to-real-order path check", decision_to_real_order_issues()),
        ("H manual-trade isolation check", manual_trade_boundary_issues()),
        ("I Stage 5 review boundary check", stage5_review_boundary_issues(schema)),
        ("J Stage 6 package structure check", stage6_package_structure_issues(schema)),
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail if production code exposes real-broker or automatic order execution."
    )
    parser.add_argument(
        "--openapi-input",
        type=Path,
        help="Check a saved OpenAPI document instead of rebuilding the local app schema.",
    )
    parser.add_argument(
        "--write-openapi",
        type=Path,
        help="Save the actual FastAPI OpenAPI document checked by this run.",
    )
    parser.add_argument(
        "--write-route-diff",
        type=Path,
        help="Write the selected 0.9.0 migration route diff as JSON.",
    )
    parser.add_argument(
        "--migration-stage",
        type=int,
        choices=(1, 2, 5, 6),
        default=1,
        help="Baseline used by --write-route-diff (default: 1).",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        schema = load_openapi_schema(args.openapi_input)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot load OpenAPI schema: {exc}")
        return 1

    failures = 0
    for label, issues in run_checks(schema):
        if issues:
            failures += len(issues)
            print(f"FAIL {label}")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print(f"PASS {label}")

    if args.write_openapi is not None:
        args.write_openapi.parent.mkdir(parents=True, exist_ok=True)
        args.write_openapi.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"WROTE OpenAPI: {args.write_openapi}")

    if args.migration_stage == 6:
        diff = stage6_route_diff(schema)
    elif args.migration_stage == 5:
        diff = stage5_route_diff(schema)
    elif args.migration_stage == 2:
        diff = stage2_route_diff(schema)
    else:
        diff = migration_route_diff(schema)
    if args.write_route_diff is not None:
        args.write_route_diff.parent.mkdir(parents=True, exist_ok=True)
        args.write_route_diff.write_text(
            json.dumps(diff, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"WROTE route diff: {args.write_route_diff}")

    diff_is_expected = (
        diff["only_expected_routes_changed"]
        if args.migration_stage in {2, 5, 6}
        else diff["only_expected_routes_removed"]
    )
    if args.write_route_diff is not None and not diff_is_expected:
        failures += 1
        print("FAIL route diff contains unexpected additions/removals")

    if failures:
        print(f"check_no_live_execution: FAILED ({failures} issue(s))")
        return 1
    print("check_no_live_execution: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
