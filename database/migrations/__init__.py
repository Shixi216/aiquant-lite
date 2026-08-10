from __future__ import annotations

from collections.abc import Callable

import duckdb

from database.migrations.v0100_unified_data import (
    apply_migration as apply_unified_data_migration,
)
from database.migrations.v0101_backfill_audit import (
    apply_migration as apply_backfill_audit_migration,
)
from database.migrations.v0102_fundamental_point_in_time import (
    apply_migration as apply_fundamental_point_in_time_migration,
)
from database.migrations.v0103_sentiment_v1 import (
    apply_migration as apply_sentiment_v1_migration,
)
from database.migrations.v0104_policy_news_v1 import (
    apply_migration as apply_policy_news_v1_migration,
)
from database.migrations.v0105_capital_flow_v1 import (
    apply_migration as apply_capital_flow_v1_migration,
)
from database.migrations.v0106_full_market_data_foundation import (
    apply_migration as apply_full_market_data_foundation_migration,
)
from database.migrations.v0107_historical_market_expansion import (
    apply_migration as apply_historical_market_expansion_migration,
)
from database.migrations.v0108_trade_date_history_shards import (
    apply_migration as apply_trade_date_history_shards_migration,
)
from database.migrations.v0109_five_factor_orchestration import (
    apply_migration as apply_five_factor_orchestration_migration,
)
from database.migrations.v0110_market_scanner_v1 import (
    apply_migration as apply_market_scanner_v1_migration,
)
from database.migrations.v0111_experiment_evaluation_v1 import (
    apply_migration as apply_experiment_evaluation_v1_migration,
)
from database.migrations.v0114_formal_history_validation import (
    apply_migration as apply_formal_history_validation_migration,
)
from database.migrations.v0115_financial_history_persistence_index import (
    apply_migration as apply_financial_history_persistence_index_migration,
)
from database.migrations.v0116_overheat_preproduction_shadow import (
    apply_migration as apply_overheat_preproduction_shadow_migration,
)
from database.migrations.v0117_freeze_overheat_shadow_b import (
    apply_migration as apply_freeze_overheat_shadow_b_migration,
)


Migration = Callable[[duckdb.DuckDBPyConnection], bool]

MIGRATIONS: tuple[Migration, ...] = (
    apply_unified_data_migration,
    apply_backfill_audit_migration,
    apply_fundamental_point_in_time_migration,
    apply_sentiment_v1_migration,
    apply_policy_news_v1_migration,
    apply_capital_flow_v1_migration,
    apply_full_market_data_foundation_migration,
    apply_historical_market_expansion_migration,
    apply_trade_date_history_shards_migration,
    apply_five_factor_orchestration_migration,
    apply_market_scanner_v1_migration,
    apply_experiment_evaluation_v1_migration,
    apply_formal_history_validation_migration,
    apply_financial_history_persistence_index_migration,
    apply_overheat_preproduction_shadow_migration,
    apply_freeze_overheat_shadow_b_migration,
)


def run_migrations(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """Apply pending migrations and return the identifiers applied in this run."""

    applied: list[str] = []
    for migration in MIGRATIONS:
        if migration(connection):
            applied.append(migration.__module__.rsplit(".", 1)[-1])
    return applied


__all__ = ["MIGRATIONS", "run_migrations"]
