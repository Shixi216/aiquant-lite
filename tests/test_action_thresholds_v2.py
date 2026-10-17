"""ActionThresholdsV2 分位数锚定测试（阈值迁移 v2）

验证：
1. 无表/读表失败时回退常量（P90≈0.42 / P75≈0.22）
2. cross_section_thresholds 表有值时覆盖回退常量
3. DecisionEngine 构造时同一 thresholds 实例注入 resolver + sizer
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import duckdb
import pytest

from trading.decision_support.action import Action
from trading.decision_support.action_resolver import (
    DecisionActionResolver,
    ResolverInput,
)
from trading.decision_support.decision_engine import DecisionEngine
from trading.decision_support.position_sizer import PositionSizer, SizerInput
from trading.decision_support.thresholds import (
    THRESHOLD_VERSION,
    ActionThresholdsV2,
)
from trading.decision_support.trade_plan_repository import TradePlanRepository

TZ = timezone(timedelta(hours=8))

_DDL = """CREATE TABLE cross_section_thresholds (
    data_cutoff TIMESTAMPTZ, p50 DOUBLE, p75 DOUBLE, p90 DOUBLE,
    p95 DOUBLE, strong_buy_q DOUBLE, buy_q DOUBLE,
    n_total BIGINT, generated_at TIMESTAMPTZ,
    PRIMARY KEY (data_cutoff))"""


@pytest.fixture
def patch_thresholds_db(monkeypatch):
    """替换 database.db.get_connection，指向临时库；None 模拟库不可用。"""

    def _patch(target_db: str | None):
        if target_db is None:
            def failing_get_connection(*, read_only: bool = False):
                raise RuntimeError("db unavailable")

            monkeypatch.setattr(
                "database.db.get_connection", failing_get_connection
            )
            return None

        con = duckdb.connect(target_db)
        con.execute(_DDL)
        con.execute(
            "INSERT INTO cross_section_thresholds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [datetime.now(tz=TZ), -0.01, 0.20, 0.41, 0.52, 0.41, 0.20, 5000,
             datetime.now(tz=TZ)],
        )
        con.close()

        @contextmanager
        def tmp_get_connection(*, read_only: bool = False):
            session = duckdb.connect(target_db, read_only=True)
            try:
                yield session
            finally:
                session.close()

        monkeypatch.setattr("database.db.get_connection", tmp_get_connection)
        return target_db

    return _patch


class TestFallbackConstants:
    def test_version(self):
        assert THRESHOLD_VERSION == "action-thresholds-v2"
        assert ActionThresholdsV2().version == "action-thresholds-v2"

    def test_fallback_on_db_error(self, patch_thresholds_db):
        patch_thresholds_db(None)
        t = ActionThresholdsV2.from_table()
        assert t.strong_buy_min == pytest.approx(0.42)
        assert t.buy_min == pytest.approx(0.22)
        assert t.wait_min == pytest.approx(-0.20)
        assert t.hold_add_min == pytest.approx(0.42)
        assert t.hold_min == pytest.approx(0.22)
        assert t.reduce_min == pytest.approx(-0.50)

    def test_no_conf_fields(self):
        """confidence 剥离：v2 无 conf 阈值字段"""
        fields = set(ActionThresholdsV2().__dataclass_fields__)
        assert "strong_buy_conf" not in fields
        assert "buy_conf" not in fields
        assert "low_confidence" not in fields


class TestTableOverride:
    def test_from_table_overrides_quantiles(self, patch_thresholds_db, tmp_path):
        patch_thresholds_db(str(tmp_path / "t.duckdb"))
        t = ActionThresholdsV2.from_table()
        assert t.strong_buy_min == pytest.approx(0.41)
        assert t.buy_min == pytest.approx(0.20)
        assert t.hold_add_min == pytest.approx(0.41)
        assert t.hold_min == pytest.approx(0.20)
        assert t.reduce_watch_max == pytest.approx(0.20)
        # 亏损保护线保持绝对值
        assert t.reduce_min == pytest.approx(-0.50)
        assert t.exit_max == pytest.approx(-0.50)

    def test_resolver_uses_table_thresholds(self, patch_thresholds_db, tmp_path):
        patch_thresholds_db(str(tmp_path / "t.duckdb"))
        resolver = DecisionActionResolver()
        assert resolver.t.strong_buy_min == pytest.approx(0.41)
        out = resolver.resolve(
            ResolverInput(point_in_time_ok=True, formal_score=0.415)
        )
        assert out.action == Action.STRONG_BUY
        assert out.action_reason_codes == ["SCORE_GE_STRONG_BUY_Q"]


class TestEngineSharedThresholds:
    def test_engine_injects_same_thresholds(self, tmp_path):
        t = ActionThresholdsV2()
        engine = DecisionEngine(
            plan_repo=TradePlanRepository(str(tmp_path / "p.duckdb")),
            thresholds=t,
        )
        assert engine.resolver.t is t
        assert engine.sizer.t is t

    def test_sizer_score_strength(self):
        t = ActionThresholdsV2()
        sizer = PositionSizer(t)
        strong = sizer.size(SizerInput(formal_score=0.50, action=Action.STRONG_BUY))
        mid = sizer.size(SizerInput(formal_score=0.30, action=Action.BUY))
        assert strong.target_position_ratio == 0.18
        assert mid.target_position_ratio == 0.12
