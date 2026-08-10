"""DecisionPacket V2（阶段4.1/任务书第二十二章）

- 不可变：保存后不可原地修改（frozen）
- 版本链：修改 → 新版本 + parent_decision_id + 新SHA-256
- 用户隔离：local_user_id
- 内容哈希：content_sha256

字段：decision_id/local_user_id/symbol/action/position_advice/trade_plan/
snapshot_id/data_cutoff/strategy_version/factor_versions/formal_score/
enhanced_score/confidence/veto_result/evidence_ids/model_call_ids/
created_at/expires_at/parent_decision_id/content_sha256
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class DecisionPacketV2:
    decision_id: str
    local_user_id: str
    symbol: str
    action: str
    position_advice: dict = field(default_factory=dict)
    trade_plan_id: str = ""
    snapshot_id: str = ""
    data_cutoff: str = ""
    strategy_version: str = "formal-60-40-v1"
    factor_versions: dict = field(default_factory=dict)
    formal_score: float = 0.0
    enhanced_score: Optional[float] = None
    confidence: float = 0.0
    veto_result: dict = field(default_factory=dict)
    evidence_ids: list = field(default_factory=list)
    model_call_ids: list = field(default_factory=list)
    created_at: str = ""
    expires_at: str = ""
    parent_decision_id: str = ""     # 版本链：父决策
    content_sha256: str = ""         # 内容哈希
    # 冻结价格区间（首次决策时生成，后续不可修改）
    reference_price: float = 0.0     # 决策时参考价
    frozen_entry_zone: list = field(default_factory=list)
    frozen_preferred_zone: list = field(default_factory=list)
    frozen_stop_loss_price: float = 0.0
    zone_generated_at: str = ""      # 区间生成时间
    zone_version: str = "V1"         # 区间版本号
    zone_valid_until: str = ""       # 区间有效期

    def compute_sha256(self) -> str:
        """内容哈希（不含 decision_id/sha256/parent，保证内容可校验）"""
        payload = {
            "local_user_id": self.local_user_id,
            "symbol": self.symbol,
            "action": self.action,
            "position_advice": self.position_advice,
            "trade_plan_id": self.trade_plan_id,
            "snapshot_id": self.snapshot_id,
            "data_cutoff": self.data_cutoff,
            "strategy_version": self.strategy_version,
            "factor_versions": self.factor_versions,
            "formal_score": self.formal_score,
            "enhanced_score": self.enhanced_score,
            "confidence": self.confidence,
            "veto_result": self.veto_result,
            "evidence_ids": sorted(self.evidence_ids),
            "created_at": self.created_at,
            "frozen_entry_zone": self.frozen_entry_zone,
            "frozen_preferred_zone": self.frozen_preferred_zone,
            "frozen_stop_loss_price": self.frozen_stop_loss_price,
            "zone_version": self.zone_version,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()


def create_decision_packet(
    *,
    local_user_id: str,
    symbol: str,
    action: str,
    formal_score: float,
    confidence: float,
    position_advice: dict | None = None,
    trade_plan_id: str = "",
    snapshot_id: str = "",
    data_cutoff: str = "",
    enhanced_score: Optional[float] = None,
    veto_result: dict | None = None,
    evidence_ids: list | None = None,
    parent_decision_id: str = "",
    reference_price: float = 0.0,
    frozen_entry_zone: list | None = None,
    frozen_preferred_zone: list | None = None,
    frozen_stop_loss_price: float = 0.0,
    zone_version: str = "V1",
) -> DecisionPacketV2:
    """创建决策包（自动生成ID/时间戳/哈希）"""
    now = datetime.now(tz=TZ)
    zone_valid_until = (now + timedelta(days=5)).isoformat()
    packet = DecisionPacketV2(
        decision_id=f"dec_{uuid.uuid4().hex[:16]}",
        local_user_id=local_user_id,
        symbol=symbol,
        action=action,
        position_advice=position_advice or {},
        trade_plan_id=trade_plan_id,
        snapshot_id=snapshot_id,
        data_cutoff=data_cutoff,
        formal_score=formal_score,
        enhanced_score=enhanced_score,
        confidence=confidence,
        veto_result=veto_result or {},
        evidence_ids=evidence_ids or [],
        created_at=now.isoformat(),
        parent_decision_id=parent_decision_id,
        reference_price=reference_price,
        frozen_entry_zone=frozen_entry_zone or [],
        frozen_preferred_zone=frozen_preferred_zone or [],
        frozen_stop_loss_price=frozen_stop_loss_price,
        zone_generated_at=now.isoformat(),
        zone_version=zone_version,
        zone_valid_until=zone_valid_until,
    )
    # 冻结哈希（object.__setattr__ 用于 frozen dataclass）
    object.__setattr__(packet, "content_sha256", packet.compute_sha256())
    return packet


class DecisionPacketV2Repository:
    """决策包持久化（不可变+版本链+用户隔离）"""

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
            CREATE TABLE IF NOT EXISTS decision_packets_v2 (
                decision_id VARCHAR PRIMARY KEY,
                local_user_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                position_advice_json VARCHAR,
                trade_plan_id VARCHAR,
                snapshot_id VARCHAR,
                data_cutoff VARCHAR,
                strategy_version VARCHAR,
                factor_versions_json VARCHAR,
                formal_score DOUBLE,
                enhanced_score DOUBLE,
                confidence DOUBLE,
                veto_result_json VARCHAR,
                evidence_ids_json VARCHAR,
                model_call_ids_json VARCHAR,
                created_at VARCHAR,
                expires_at VARCHAR,
                parent_decision_id VARCHAR,
                content_sha256 VARCHAR NOT NULL,
                status VARCHAR DEFAULT 'SAVED',
                reference_price DOUBLE DEFAULT 0,
                frozen_entry_zone_json VARCHAR,
                frozen_preferred_zone_json VARCHAR,
                frozen_stop_loss_price DOUBLE DEFAULT 0,
                zone_generated_at VARCHAR,
                zone_version VARCHAR DEFAULT 'V1',
                zone_valid_until VARCHAR
            )
        """)
        return con

    def save(self, packet: DecisionPacketV2) -> DecisionPacketV2:
        """保存（不可变——若存在同ID则拒绝覆盖）"""
        import json as _json
        con = self._connect()
        try:
            exists = con.execute(
                "SELECT 1 FROM decision_packets_v2 WHERE decision_id=?",
                [packet.decision_id],
            ).fetchone()
            if exists:
                raise ValueError(f"DecisionPacket {packet.decision_id} 已存在，不可覆盖（不可变）")
            con.execute(
                """
                INSERT INTO decision_packets_v2
                (decision_id, local_user_id, symbol, action, position_advice_json,
                 trade_plan_id, snapshot_id, data_cutoff, strategy_version,
                 factor_versions_json, formal_score, enhanced_score, confidence,
                 veto_result_json, evidence_ids_json, model_call_ids_json,
                 created_at, expires_at, parent_decision_id, content_sha256, status,
                 reference_price, frozen_entry_zone_json, frozen_preferred_zone_json,
                 frozen_stop_loss_price, zone_generated_at, zone_version, zone_valid_until)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SAVED',
                        ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    packet.decision_id, packet.local_user_id, packet.symbol, packet.action,
                    _json.dumps(packet.position_advice, ensure_ascii=False),
                    packet.trade_plan_id, packet.snapshot_id, packet.data_cutoff,
                    packet.strategy_version,
                    _json.dumps(packet.factor_versions, ensure_ascii=False),
                    packet.formal_score, packet.enhanced_score, packet.confidence,
                    _json.dumps(packet.veto_result, ensure_ascii=False),
                    _json.dumps(packet.evidence_ids, ensure_ascii=False),
                    _json.dumps(packet.model_call_ids, ensure_ascii=False),
                    packet.created_at, packet.expires_at,
                    packet.parent_decision_id, packet.content_sha256,
                    packet.reference_price,
                    _json.dumps(packet.frozen_entry_zone, ensure_ascii=False),
                    _json.dumps(packet.frozen_preferred_zone, ensure_ascii=False),
                    packet.frozen_stop_loss_price,
                    packet.zone_generated_at,
                    packet.zone_version,
                    packet.zone_valid_until,
                ],
            )
        finally:
            con.close()
        return packet

    def get(self, decision_id: str) -> Optional[DecisionPacketV2]:
        import json as _json
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM decision_packets_v2 WHERE decision_id=?",
                [decision_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return self._row_to_packet(row)

    def list_by_user(self, local_user_id: str, limit: int = 50) -> list[DecisionPacketV2]:
        """按用户列出决策（用户隔离）"""
        import json as _json
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM decision_packets_v2 WHERE local_user_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                [local_user_id, limit],
            ).fetchall()
        finally:
            con.close()
        return [self._row_to_packet(r) for r in rows]

    def list_versions(self, local_user_id: str, symbol: str) -> list[DecisionPacketV2]:
        """版本链：同一股票的全部决策版本"""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT * FROM decision_packets_v2 WHERE local_user_id=? AND symbol=? "
                "ORDER BY created_at",
                [local_user_id, symbol],
            ).fetchall()
        finally:
            con.close()
        return [self._row_to_packet(r) for r in rows]

    def _row_to_packet(self, row) -> DecisionPacketV2:
        import json as _json
        return DecisionPacketV2(
            decision_id=row[0], local_user_id=row[1], symbol=row[2], action=row[3],
            position_advice=_json.loads(row[4]) if row[4] else {},
            trade_plan_id=row[5] or "", snapshot_id=row[6] or "",
            data_cutoff=row[7] or "", strategy_version=row[8] or "",
            factor_versions=_json.loads(row[9]) if row[9] else {},
            formal_score=row[10] or 0, enhanced_score=row[11],
            confidence=row[12] or 0,
            veto_result=_json.loads(row[13]) if row[13] else {},
            evidence_ids=_json.loads(row[14]) if row[14] else [],
            model_call_ids=_json.loads(row[15]) if row[15] else [],
            created_at=row[16] or "", expires_at=row[17] or "",
            parent_decision_id=row[18] or "", content_sha256=row[19] or "",
 reference_price=row[21] or 0,
 frozen_entry_zone=_json.loads(row[22]) if row[22] else [],
 frozen_preferred_zone=_json.loads(row[23]) if row[23] else [],
 frozen_stop_loss_price=row[24] or 0,
 zone_generated_at=row[25] or "",
 zone_version=row[26] or "V1",
 zone_valid_until=row[27] or "",
 )

    def create_revision(self, parent: DecisionPacketV2, **changes) -> DecisionPacketV2:
        """创建新版本（保留 parent_decision_id 链）"""
        new_packet = create_decision_packet(
            local_user_id=parent.local_user_id,
            symbol=parent.symbol,
            action=changes.get("action", parent.action),
            formal_score=changes.get("formal_score", parent.formal_score),
            confidence=changes.get("confidence", parent.confidence),
            position_advice=changes.get("position_advice", parent.position_advice),
            trade_plan_id=changes.get("trade_plan_id", parent.trade_plan_id),
            snapshot_id=changes.get("snapshot_id", parent.snapshot_id),
            data_cutoff=changes.get("data_cutoff", parent.data_cutoff),
            enhanced_score=changes.get("enhanced_score", parent.enhanced_score),
            veto_result=changes.get("veto_result", parent.veto_result),
            evidence_ids=changes.get("evidence_ids", parent.evidence_ids),
            parent_decision_id=parent.decision_id,
        )
        return new_packet
