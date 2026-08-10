"""统一任务上下文 TaskContext（阶段1.1）

所有 Scanner、RESEARCH、DECISION、持仓分析和组合分析必须接收统一 TaskContext。
禁止各模块自行生成互不一致的 cutoff 或 snapshot_id。

字段（15项）：
- local_user_id：业务数据隔离主键
- external_user_id：外部渠道用户ID（企微/QQ/桌面端）
- channel：渠道（wecom/qq/desktop/api）
- session_id
- task_id
- mode：SCREENING / RESEARCH / DECISION / POSITION_DIAGNOSIS / PORTFOLIO_DIAGNOSIS
- symbol：股票代码
- snapshot_id：固定快照编号
- trade_date：行情实际所属交易日
- snapshot_time：快照生成时间
- collected_at：数据采集时间
- data_cutoff：本次分析允许使用的数据截止时间
- strategy_version：正式策略版本
- decision_version：决策逻辑版本
- created_at：任务创建时间
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

TZ = timezone(timedelta(hours=8))

DEFAULT_STRATEGY_VERSION = "formal-60-40-v1"
DEFAULT_DECISION_VERSION = "decision-v1"


@dataclass(frozen=True)
class TaskContext:
    local_user_id: str
    external_user_id: str
    channel: str = "wecom"
    session_id: str = ""
    task_id: str = ""
    mode: str = "RESEARCH"
    symbol: str = ""
    snapshot_id: str = ""
    trade_date: str = ""            # YYYY-MM-DD 或 YYYYMMDD
    snapshot_time: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    data_cutoff: Optional[datetime] = None
    strategy_version: str = DEFAULT_STRATEGY_VERSION
    decision_version: str = DEFAULT_DECISION_VERSION
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=TZ))

    def __post_init__(self) -> None:
        if not self.task_id:
            object.__setattr__(self, "task_id", f"task_{uuid.uuid4().hex[:16]}")
        if not self.session_id:
            object.__setattr__(self, "session_id", f"sess_{uuid.uuid4().hex[:12]}")
        if self.data_cutoff is None:
            object.__setattr__(self, "data_cutoff", datetime.now(tz=TZ))
        if self.snapshot_time is None:
            object.__setattr__(self, "snapshot_time", datetime.now(tz=TZ))
        if self.collected_at is None:
            object.__setattr__(self, "collected_at", datetime.now(tz=TZ))

    def with_symbol(self, symbol: str) -> "TaskContext":
        """返回带 symbol 的新上下文（保持 snapshot_id/data_cutoff 不变）"""
        return TaskContext(
            local_user_id=self.local_user_id,
            external_user_id=self.external_user_id,
            channel=self.channel,
            session_id=self.session_id,
            task_id=self.task_id,
            mode=self.mode,
            symbol=symbol,
            snapshot_id=self.snapshot_id,
            trade_date=self.trade_date,
            snapshot_time=self.snapshot_time,
            collected_at=self.collected_at,
            data_cutoff=self.data_cutoff,
            strategy_version=self.strategy_version,
            decision_version=self.decision_version,
            created_at=self.created_at,
        )

    def as_dict(self) -> dict:
        return {
            "local_user_id": self.local_user_id,
            "external_user_id": self.external_user_id,
            "channel": self.channel,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "mode": self.mode,
            "symbol": self.symbol,
            "snapshot_id": self.snapshot_id,
            "trade_date": self.trade_date,
            "snapshot_time": self.snapshot_time.isoformat() if self.snapshot_time else None,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
            "data_cutoff": self.data_cutoff.isoformat() if self.data_cutoff else None,
            "strategy_version": self.strategy_version,
            "decision_version": self.decision_version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
