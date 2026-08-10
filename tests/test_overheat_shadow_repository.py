from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config.settings import settings
from database.db import get_connection
from trading.research.overheat_preproduction_shadow import (
    OverheatShadowRepository,
)


def test_repository_insert_is_research_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "opc_database_path",
        tmp_path / "overheat-shadow.duckdb",
    )
    repository = OverheatShadowRepository()
    now = datetime(2026, 8, 7, 15, 30, tzinfo=timezone(timedelta(hours=8)))
    repository.save_observation(
        {
            "observation_id": "ohs_test",
            "symbol": "600000.SH",
            "observed_at": now,
            "data_cutoff": now,
            "source_bar_id": "bar_test",
            "source_close": 10.0,
            "bar_history_count": 65,
            "feature_status": "AVAILABLE",
            "original_technical_score": 0.8,
            "shadow_technical_score": 0.6,
            "ma20_ma60_expansion": 0.21,
            "ma60_slope_5": 0.05,
            "expansion_penalty": 0.1,
            "ma60_slope_penalty": 0.1,
            "total_penalty": 0.2,
            "a_formal_score": 0.68,
            "b_shadow_formal_score": 0.56,
            "a_action": "buy",
            "b_shadow_action": "buy",
            "action_diverged": False,
            "hard_veto": False,
            "official_result_hash": "0" * 64,
            "created_at": now,
        }
    )
    with get_connection() as connection:
        assert connection.execute(
            """
            SELECT research_only, affects_production, creates_trade_records
            FROM overheat_shadow_observations
            WHERE observation_id = 'ohs_test'
            """
        ).fetchone() == (True, False, False)
