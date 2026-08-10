from __future__ import annotations

import importlib
from pathlib import Path

from scripts.check_no_live_execution import (
    STAGE6_LEGACY_MODULES,
    STAGE6_REQUIRED_PACKAGES,
    stage6_package_structure_issues,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_target_package_tree_exists() -> None:
    for relative in STAGE6_REQUIRED_PACKAGES:
        package = PROJECT_ROOT / relative
        assert package.is_dir()
        assert (package / "__init__.py").is_file()


def test_legacy_root_modules_are_removed() -> None:
    for module in STAGE6_LEGACY_MODULES:
        path = PROJECT_ROOT.joinpath(*module.split(".")).with_suffix(".py")
        assert not path.exists()


def test_new_internal_modules_are_importable() -> None:
    modules = (
        "trading.research.technical",
        "trading.research.fundamental",
        "trading.research.events.position_data",
        "trading.decision_support.orchestrator",
        "trading.decision_support.risk_rules",
        "trading.decision_support.adversarial_review",
        "trading.decision_support.decision_packets",
        "trading.simulation.backtest",
        "trading.simulation.paper_account",
        "trading.simulation.portfolio",
        "trading.simulation.hypothetical_orders",
        "trading.simulation.service",
        "trading.review.daily",
        "trading.review.attribution",
        "manual_tracking.trades",
        "manual_tracking.confirmations",
        "manual_tracking.risk_reviews",
    )
    for module in modules:
        assert importlib.import_module(module) is not None


def test_stage6_structure_guard_passes() -> None:
    assert stage6_package_structure_issues() == []
