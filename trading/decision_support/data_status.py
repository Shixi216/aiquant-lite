"""统一数据状态枚举（阶段1.4）

状态：FRESH（新鲜）/ DEGRADED（降级）/ STALE（陈旧）/ FAILED（失败）

规则：
- STALE：禁止Scanner推荐、建仓和买入建议
- FAILED：停止正式决策
- DEGRADED：允许分析，但降低置信度和仓位级别
- FRESH：正常决策

废除"永远最新"作为代码状态或用户输出。
"""
from __future__ import annotations

from enum import StrEnum


class DataStatus(StrEnum):
    FRESH = "FRESH"            # 数据新鲜、覆盖完整
    DEGRADED = "DEGRADED"      # 部分非核心数据缺失
    STALE = "STALE"            # 核心行情数据陈旧
    FAILED = "FAILED"          # 核心数据获取失败


# 中文展示映射
DATA_STATUS_ZH = {
    DataStatus.FRESH: "数据新鲜",
    DataStatus.DEGRADED: "数据降级",
    DataStatus.STALE: "数据陈旧",
    DataStatus.FAILED: "数据失败",
}


def data_status_zh(status: DataStatus | str) -> str:
    """数据状态中文输出"""
    if isinstance(status, str):
        try:
            status = DataStatus(status)
        except ValueError:
            return str(status)
    return DATA_STATUS_ZH.get(status, str(status))


def status_priority(status: DataStatus) -> int:
    """状态优先级（数值越大越严重）"""
    return {
        DataStatus.FRESH: 0,
        DataStatus.DEGRADED: 1,
        DataStatus.STALE: 2,
        DataStatus.FAILED: 3,
    }.get(status, 0)
