from __future__ import annotations

import duckdb

from database.migrations.v0116_overheat_preproduction_shadow import (
    apply_migration as apply_v0116,
)
from database.migrations.v0117_freeze_overheat_shadow_b import (
    MIGRATION_ID,
    apply_migration as apply_v0117,
)
from trading.research.overheat_preproduction_shadow import (
    EXPECTED_FROZEN_PARAMETER_HASH,
    FIXED_CALIBRATION,
    FROZEN_PARAMETER_HASH,
    SHADOW_CODE_VERSION,
    SHADOW_EFFECTIVE_AT,
    SHADOW_FORMULA,
    SHADOW_VERSION,
    frozen_parameter_payload,
)


def test_b_shadow_version_and_parameter_hash_are_frozen() -> None:
    assert SHADOW_VERSION == "B-OVERHEAT-1.0.0"
    assert SHADOW_CODE_VERSION == "overheat-preproduction-shadow-v1"
    assert SHADOW_EFFECTIVE_AT.isoformat() == "2026-08-10T11:52:42+08:00"
    assert FROZEN_PARAMETER_HASH == EXPECTED_FROZEN_PARAMETER_HASH
    assert FROZEN_PARAMETER_HASH == (
        "b976fa673c49520732825787b5da52ec792d8792ac378549fcb7d683a837e1bb"
    )


def test_frozen_payload_keeps_formula_and_excludes_expansion_speed() -> None:
    payload = frozen_parameter_payload()
    assert payload["formula"] == SHADOW_FORMULA
    assert payload["expansion_speed_penalized"] is False
    assert payload["calibration"] == FIXED_CALIBRATION.as_dict()
    assert "expansion_speed" not in str(payload["calibration"])


def test_freeze_migration_is_idempotent_and_registers_one_version() -> None:
    connection = duckdb.connect(":memory:")
    assert apply_v0116(connection) is True
    assert apply_v0117(connection) is True
    assert apply_v0117(connection) is False
    row = connection.execute(
        """
        SELECT shadow_version, parameter_hash, effective_at, code_version,
               frozen, minimum_observation_trade_days,
               target_observation_trade_days, research_only,
               affects_production
        FROM overheat_shadow_versions
        """
    ).fetchone()
    assert row[0] == SHADOW_VERSION
    assert row[1] == FROZEN_PARAMETER_HASH
    assert row[3] == SHADOW_CODE_VERSION
    assert row[4:] == (True, 20, 30, True, False)
    assert connection.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
        [MIGRATION_ID],
    ).fetchone()[0] == 1
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info('overheat_shadow_observations')"
        ).fetchall()
    }
    assert {
        "shadow_version", "parameter_hash", "shadow_code_version"
    } <= columns
