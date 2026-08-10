"""TradePlan 持久化（阶段2.1）— 显式 local_user_id 隔离"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from trading.decision_support.trade_plan import TradePlan, TradePlanStatus

TZ = timezone(timedelta(hours=8))


class TradePlanRepository:
    def __init__(self, db_path: Path | None = None):
        from config.settings import settings
        if db_path is None:
            db_path = Path(settings.opc_database_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[2] / db_path
        self.db_path = db_path

    def _connect(self):
        from database.db import open_database
        con = open_database(self.db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS trade_plans (
                plan_id VARCHAR PRIMARY KEY,
                local_user_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                stock_name VARCHAR,
                action VARCHAR NOT NULL,
                current_position DOUBLE DEFAULT 0,
                recommended_position DOUBLE DEFAULT 0,
                position_change DOUBLE DEFAULT 0,
                current_price DOUBLE DEFAULT 0,
                entry_zone_json VARCHAR,
                add_zone_json VARCHAR,
                reduce_zone_json VARCHAR,
                stop_loss_price DOUBLE,
                stop_loss_condition VARCHAR,
                take_profit_zone_json VARCHAR,
                invalidation_condition VARCHAR,
                expected_holding_period VARCHAR,
                risk_reward_ratio DOUBLE DEFAULT 0,
                formal_score DOUBLE DEFAULT 0,
                enhanced_score DOUBLE,
                confidence DOUBLE DEFAULT 0,
                data_status VARCHAR DEFAULT 'FRESH',
                snapshot_id VARCHAR,
                data_cutoff VARCHAR,
                strategy_version VARCHAR,
                veto_status VARCHAR DEFAULT 'NO_VETO',
                supporting_evidence_json VARCHAR,
                major_risks_json VARCHAR,
                status VARCHAR DEFAULT 'DRAFT',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP WITH TIME ZONE
            )
        """)
        return con

    @staticmethod
    def _json(v) -> str | None:
        return json.dumps(v, ensure_ascii=False) if v is not None else None

    @staticmethod
    def _load(s) -> list:
        if not s:
            return []
        try:
            return json.loads(s)
        except Exception:
            return []

    def create_plan(self, plan: TradePlan) -> TradePlan:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO trade_plans
                (plan_id, local_user_id, symbol, stock_name, action,
                 current_position, recommended_position, position_change, current_price,
                 entry_zone_json, add_zone_json, reduce_zone_json,
                 stop_loss_price, stop_loss_condition, take_profit_zone_json,
                 invalidation_condition, expected_holding_period, risk_reward_ratio,
                 formal_score, enhanced_score, confidence, data_status,
                 snapshot_id, data_cutoff, strategy_version, veto_status,
                 supporting_evidence_json, major_risks_json, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    plan.plan_id, plan.local_user_id, plan.symbol, plan.stock_name, plan.action,
                    plan.current_position, plan.recommended_position, plan.position_change, plan.current_price,
                    self._json(plan.entry_zone), self._json(plan.add_zone), self._json(plan.reduce_zone),
                    plan.stop_loss_price, plan.stop_loss_condition, self._json(plan.take_profit_zone),
                    plan.invalidation_condition, plan.expected_holding_period, plan.risk_reward_ratio,
                    plan.formal_score, plan.enhanced_score, plan.confidence, plan.data_status,
                    plan.snapshot_id, plan.data_cutoff, plan.strategy_version, plan.veto_status,
                    self._json(plan.supporting_evidence), self._json(plan.major_risks),
                    plan.status.value, plan.created_at, plan.expires_at,
                ],
            )
        finally:
            con.close()
        return plan

    def get_plan(self, plan_id: str) -> Optional[TradePlan]:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM trade_plans WHERE plan_id=?", [plan_id]).fetchone()
        finally:
            con.close()
        return self._row_to_plan(row) if row else None

    def list_plans(self, local_user_id: str, status: str | None = None) -> list[TradePlan]:
        """按用户隔离列出交易计划"""
        con = self._connect()
        try:
            if status:
                rows = con.execute(
                    "SELECT * FROM trade_plans WHERE local_user_id=? AND status=? ORDER BY created_at DESC",
                    [local_user_id, status],
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM trade_plans WHERE local_user_id=? ORDER BY created_at DESC",
                    [local_user_id],
                ).fetchall()
        finally:
            con.close()
        return [self._row_to_plan(r) for r in rows]

    def update_status(self, plan_id: str, status: TradePlanStatus) -> None:
        con = self._connect()
        try:
            con.execute(
                "UPDATE trade_plans SET status=? WHERE plan_id=?",
                [status.value, plan_id],
            )
        finally:
            con.close()

    def delete_plan(self, plan_id: str, local_user_id: str) -> bool:
        """删除（限本人）"""
        con = self._connect()
        try:
            cur = con.execute(
                "DELETE FROM trade_plans WHERE plan_id=? AND local_user_id=?",
                [plan_id, local_user_id],
            )
            return cur.fetchone() is not None
        finally:
            con.close()

    def _row_to_plan(self, row) -> TradePlan:
        """按列顺序解析（SELECT * 顺序与表定义一致）"""
        return TradePlan(
            plan_id=row[0], local_user_id=row[1], symbol=row[2], stock_name=row[3],
            action=row[4], current_position=row[5], recommended_position=row[6],
            position_change=row[7], current_price=row[8],
            entry_zone=self._load(row[9]), add_zone=self._load(row[10]),
            reduce_zone=self._load(row[11]), stop_loss_price=row[12],
            stop_loss_condition=row[13], take_profit_zone=self._load(row[14]),
            invalidation_condition=row[15], expected_holding_period=row[16],
            risk_reward_ratio=row[17], formal_score=row[18], enhanced_score=row[19],
            confidence=row[20], data_status=row[21], snapshot_id=row[22],
            data_cutoff=row[23], strategy_version=row[24], veto_status=row[25],
            supporting_evidence=self._load(row[26]), major_risks=self._load(row[27]),
            status=TradePlanStatus(row[28]) if row[28] else TradePlanStatus.DRAFT,
            created_at=row[29], expires_at=row[30],
        )
