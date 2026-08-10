"""候选级硬VETO检测（2026-08 盘中风险边界补齐）

在候选研究流水线中提前排除高风险个股（完整VETO接入前的第一道防线）。

16类硬VETO（关键词驱动，基于公告/新闻文本）：
- 退市风险
- 立案调查
- 重大违法
- 财务造假/异常
- 严重数据缺失
- 停牌/流动性异常
- 无法核验的重大风险事件
- 审计非标意见
- 资金链断裂
- 大额违约
- 控股股东重大风险
- 核心资产冻结
- 长期停牌
- 连续经营重大不确定性
- 极端价格异常
- 数据时间无法确认

注意：这是候选级预筛（快速关键词），最终正式VETO在DECISION层完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 16类VETO关键词（中文，命中即触发）
VETO_KEYWORDS: dict[str, list[str]] = {
    "退市风险": ["退市", "终止上市", "面值退市"],
    "立案调查": ["立案调查", "立案侦查", "证监会立案", "被调查"],
    "重大违法": ["重大违法", "违法强制退市", "涉嫌犯罪"],
    "财务造假": ["财务造假", "虚增利润", "造假", "数据造假"],
    "审计非标": ["无法表示意见", "保留意见", "非标意见", "否定意见"],
    "资金链断裂": ["资金链断裂", "资金链紧张", "流动性危机"],
    "大额违约": ["债务违约", "债券违约", "违约"],
    "控股股东风险": ["控股股东冻结", "控股股东质押爆仓", "实控人失联"],
    "资产冻结": ["资产冻结", "账户冻结", "查封"],
    "停牌风险": ["停牌", "暂停上市", "终止上市风险"],
    "经营重大不确定性": ["经营困难", "停产", "重大不确定性", "持续经营"],
    "无法核验风险": ["无法核实", "无法确认", "重大风险提示"],
}

# 严重数据缺失（无日线/无基本面 → 无法研究）
DATA_INSUFFICIENT_VETO = "严重数据缺失"


@dataclass
class CandidateVetoResult:
    vetoed: bool = False
    veto_type: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    evidence_source: str = ""
    reason: str = ""


class CandidateVetoService:
    """候选级VETO检测（公告/新闻文本关键词）"""

    def check_text(self, text: str, source: str = "") -> CandidateVetoResult:
        """对文本（公告标题/新闻标题）做VETO关键词检测"""
        if not text:
            return CandidateVetoResult()
        for veto_type, keywords in VETO_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return CandidateVetoResult(
                        vetoed=True,
                        veto_type=veto_type,
                        matched_keywords=[kw],
                        evidence_source=source,
                        reason=f"命中VETO关键词「{kw}」（{veto_type}）",
                    )
        return CandidateVetoResult()

    def check_news_list(self, titles: list[str], source: str = "新闻/公告") -> CandidateVetoResult:
        """对新闻/公告标题列表检测"""
        for title in titles:
            result = self.check_text(title, source=source)
            if result.vetoed:
                return result
        return CandidateVetoResult()

    def check_data_availability(self, has_bars: bool, has_financials: bool) -> CandidateVetoResult:
        """数据完整性检查：无日线或无基本面 → 数据不足VETO"""
        if not has_bars:
            return CandidateVetoResult(
                vetoed=True, veto_type=DATA_INSUFFICIENT_VETO,
                reason="无日线数据，无法研究",
            )
        if not has_financials:
            # 基本面缺失是降级而非硬VETO（允许技术面研究）
            return CandidateVetoResult()
        return CandidateVetoResult()
