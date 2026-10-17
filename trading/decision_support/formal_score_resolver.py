"""formal_score 单一来源解析器（方案 B）

所有入口（canonical decision input / stock report / /from-data）统一通过
本解析器获取 formal_score，消灭各自实现的读表双轨。

来源优先级：
1. cross_section_4d —— 四维横截面 composite（技术30/基本30/情绪25/资金15
   的 rank 加权，rank 分位数语义），由 scripts.compute_cross_section_snapshot.py
   盘后预计算写入。带 data_cutoff 时按 PIT 语义读 history 表（不读未来截面）。
2. legacy_60_40_fallback —— 技术分*0.6 + 基本分*0.4（绝对评分语义降级路径，
   degraded=True 明确标注，供对照与告警）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CROSS_SECTION_SOURCE = "cross_section_4d"
LEGACY_SOURCE = "legacy_60_40_fallback"


@dataclass(frozen=True)
class FormalScoreResult:
    score: float
    source: str
    degraded: bool = False
    detail: dict | None = None  # 四维 rank 明细（cross_section 且请求时返回）


class FormalScoreResolver:
    def resolve(
        self,
        symbol: str,
        technical_score: float,
        fundamental_score: float,
        *,
        data_cutoff: datetime | None = None,
        with_detail: bool = False,
    ) -> FormalScoreResult:
        cross = self.read_cross_section(
            symbol, data_cutoff=data_cutoff, with_detail=with_detail
        )
        if cross is not None:
            score, detail = cross
            return FormalScoreResult(
                score=score,
                source=CROSS_SECTION_SOURCE,
                degraded=False,
                detail=detail,
            )
        return FormalScoreResult(
            score=technical_score * 0.60 + fundamental_score * 0.40,
            source=LEGACY_SOURCE,
            degraded=True,
        )

    def read_cross_section(
        self,
        symbol: str,
        *,
        data_cutoff: datetime | None = None,
        with_detail: bool = False,
    ) -> tuple[float, dict | None] | None:
        """读四维横截面 composite；读不到返回 None（调用方走降级）。

        data_cutoff 给定时从 history 表按 PIT 读取（<= cutoff 的最近截面）；
        否则读主表（仅含最新截面）。
        """
        columns = (
            "tech_rank, fund_rank, sent_rank, margin_rank, composite_score"
            if with_detail
            else "composite_score"
        )
        like = f"{symbol}.%" if "." not in symbol else symbol
        try:
            from database.db import get_connection
            with get_connection(read_only=True) as connection:
                if data_cutoff is not None:
                    row = connection.execute(
                        f"SELECT {columns} FROM cross_section_factor_snapshot_history "
                        "WHERE (symbol=? OR symbol LIKE ?) AND data_cutoff <= ? "
                        "AND composite_score IS NOT NULL "
                        "ORDER BY data_cutoff DESC, generated_at DESC LIMIT 1",
                        [symbol, like, data_cutoff],
                    ).fetchone()
                else:
                    row = connection.execute(
                        f"SELECT {columns} FROM cross_section_factor_snapshot "
                        "WHERE (symbol=? OR symbol LIKE ?) AND composite_score IS NOT NULL "
                        "ORDER BY generated_at DESC LIMIT 1",
                        [symbol, like],
                    ).fetchone()
        except Exception:
            return None
        if not row or row[-1] is None:
            return None
        if with_detail:
            detail = {
                "tech_rank": row[0],
                "fund_rank": row[1],
                "sent_rank": row[2],
                "margin_rank": row[3],
                "composite": row[4],
            }
            return float(row[4]), detail
        return float(row[0]), None


__all__ = [
    "CROSS_SECTION_SOURCE",
    "FormalScoreResolver",
    "FormalScoreResult",
    "LEGACY_SOURCE",
]
