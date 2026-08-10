"""统一查询意图路由 + 双时间轴 + 两阶段选股流程（2026-08 实时路由修复）

意图分类：
- INTRADAY_SCAN：盘中实时扫描（今天有什么好票/现在什么强/涨停/放量）
- EOD_SCAN：收盘后/历史条件扫描（收盘后选股/最新日线选股）
- RESEARCH：个股五维研究
- DECISION：结合持仓/评分/VETO/仓位的正式决策

双时间轴（同一任务必须同时携带）：
- realtime_snapshot_id / realtime_snapshot_time / realtime_data_cutoff
- latest_completed_trade_date / daily_snapshot_id / daily_data_cutoff
- analysis_horizon

两阶段：
- 阶段1：盘中候选生成（仅实时确定性指标，不调用大模型、不逐股网络）
- 阶段2：候选研究（日线/基本面/公告/新闻/风控）→ 正式判断
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import StrEnum
from typing import Optional

TZ = timezone(timedelta(hours=8))


class AnalysisHorizon(StrEnum):
    INTRADAY_SCAN = "INTRADAY_SCAN"   # 盘中实时扫描
    EOD_SCAN = "EOD_SCAN"             # 收盘后/历史扫描
    RESEARCH = "RESEARCH"             # 个股研究
    DECISION = "DECISION"             # 正式决策


# 意图关键词（优先级从高到低）
_INTRADAY_KEYWORDS = [
    "今天有什么好票", "今天买什么", "现在什么股票强", "实时涨幅",
    "盘中异动", "涨停", "放量", "当前可以买什么", "今日强势",
    "今天什么涨", "盘中", "实时",
]
_EOD_KEYWORDS = [
    "收盘后", "最新日线", "完整交易日", "历史条件", "昨日", "上周",
    "选股", "筛选",
]


@dataclass
class RoutedQuery:
    original_query: str
    horizon: AnalysisHorizon
    analysis_mode: str = "SCREENING"
    symbol: str = ""
    # 双时间轴
    realtime_snapshot_id: str = ""
    realtime_snapshot_time: str = ""
    realtime_data_cutoff: str = ""
    latest_completed_trade_date: str = ""
    daily_snapshot_id: str = ""
    daily_data_cutoff: str = ""
    # 两阶段状态
    candidates_generated: bool = False
    candidates_count: int = 0
    researched_count: int = 0
    complete_count: int = 0
    qualified_count: int = 0
    degraded_count: int = 0
    pending_count: int = 0


def route_query(query: str) -> AnalysisHorizon:
    """把自然语言查询路由到意图（DECISION > INTRADAY > EOD > RESEARCH）"""
    q = query.strip()

    # 明确决策词 → DECISION（最高优先级，含"能不能买/该不该"）
    if re.search(r"买入|卖出|建仓|加仓|减仓|清仓|止损|止盈|能不能买|该不该|能买吗|要买吗", q):
        return AnalysisHorizon.DECISION

    # 个股代码/名称 → RESEARCH
    if re.search(r"分析|研究|怎么样|走势|基本面|财报", q):
        return AnalysisHorizon.RESEARCH

    # 盘中关键词
    for kw in _INTRADAY_KEYWORDS:
        if kw in q:
            return AnalysisHorizon.INTRADAY_SCAN

    # 收盘后关键词
    for kw in _EOD_KEYWORDS:
        if kw in q:
            return AnalysisHorizon.EOD_SCAN

    # 默认：包含"股票/选股/扫描" → EOD（静态），否则 RESEARCH
    if re.search(r"股票|选股|扫描|筛选|候选", q):
        return AnalysisHorizon.EOD_SCAN
    return AnalysisHorizon.RESEARCH


# =====================================================================
# 双时间轴：组装 + 输出
# =====================================================================
@dataclass
class DualTimeline:
    """双时间轴数据（实时 vs 已完成日线）"""
    realtime_time: str = ""                 # 实时行情时间（如 8月3日10:35）
    daily_trade_date: str = ""              # 已完成日线日期（如 7月31日）
    news_cutoff: str = ""                   # 新闻公告截止
    realtime_status: str = "FRESH"          # FRESH/DEGRADED/STALE_REALTIME
    daily_status: str = "FRESH"
    analysis_horizon: str = "INTRADAY_SCAN"

    def render(self) -> str:
        """统一时间轴说明"""
        parts = []
        if self.realtime_time:
            parts.append(f"实时行情截至{self.realtime_time}")
        if self.daily_trade_date:
            parts.append(f"日线技术指标截至{self.daily_trade_date}收盘")
        if self.news_cutoff:
            parts.append(f"新闻公告截止{self.news_cutoff}")
        if not parts:
            return "（时间信息缺失）"
        return "；".join(parts) + "。"


# =====================================================================
# 两阶段流程编排
# =====================================================================
class TwoStagePipeline:
    """两阶段选股：盘中候选 → 正式研究判断"""

    def __init__(self, realtime_service=None):
        self.realtime_service = realtime_service

    # 阶段1：盘中候选生成（实时确定性指标）
    def generate_intraday_candidates(
        self, quotes, min_pct: float = 2.0, max_pct: float = 9.5,
        min_amount_wan: float = 8000.0, min_turnover: float = 1.5,
        max_candidates: int = 50, exclude_st: bool = True,
    ) -> list[dict]:
        """仅用实时确定性指标筛选 20-50 只候选（无模型、无逐股网络）"""
        cands = []
        for q in quotes:
            if q.price <= 0:
                continue
            if exclude_st and "ST" in q.name:
                continue
            if not (min_pct <= q.pct_chg <= max_pct):
                continue
            if q.amount_wan < min_amount_wan:
                continue
            if q.turnover < min_turnover:
                continue
            cands.append({
                "symbol": q.symbol,
                "name": q.name,
                "price": q.price,
                "pct_chg": q.pct_chg,
                "volume_ratio": q.volume_ratio,
                "turnover": q.turnover,
                "amount_wan": q.amount_wan,
                "high": q.high,
                "low": q.low,
                "open": q.open,
                "prev_close": q.prev_close,
            })
        # 排序：涨幅优先，兼顾量比
        cands.sort(key=lambda c: (-c["pct_chg"], -c["volume_ratio"]))
        return cands[:max_candidates]

    # 盘中强度（intraday_strength）——只基于今日实时
    @staticmethod
    def intraday_strength(c: dict) -> dict:
        """今日盘中强弱（与波段质量分离）"""
        pct = c["pct_chg"]
        vr = c["volume_ratio"]
        to = c["turnover"]
        amount = c["amount_wan"]

        # 分时强度评分（0-1）
        score = 0.0
        if pct >= 9.0:
            score += 0.6        # 接近涨停
        elif pct >= 5.0:
            score += 0.45
        elif pct >= 2.0:
            score += 0.3
        elif pct > 0:
            score += 0.15
        if vr >= 3:
            score += 0.25       # 显著放量
        elif vr >= 2:
            score += 0.15
        elif vr >= 1.5:
            score += 0.08
        if to >= 5:
            score += 0.15       # 高换手活跃
        elif to >= 3:
            score += 0.1

        score = min(score, 1.0)
        if pct >= 9.0 and vr >= 3:
            level = "极强"
        elif score >= 0.6:
            level = "强"
        elif score >= 0.35:
            level = "中等"
        else:
            level = "弱"

        crowded = vr >= 5 or to >= 15       # 拥挤度
        chase_ok = pct < 7 and vr < 4 and not crowded   # 是否适合追涨

        return {
            "score": round(score, 2),
            "level": level,
            "volume_spike": vr >= 2,
            "crowded": crowded,
            "chase_ok": chase_ok,
            "note": "今日盘中强弱，不代表中期趋势",
        }

    # 波段质量（swing_quality）——基于已完成日线 + 基本面
    @staticmethod
    def swing_quality(technical_score: float, fundamental_score: float,
                      formal_score: float, bars_info: dict | None = None) -> dict:
        """波段质量（与盘中强度分离）"""
        # 趋势结构（日线）
        trend_ok = technical_score > 0
        fundamental_ok = fundamental_score > 0

        if formal_score >= 0.4 and trend_ok and fundamental_ok:
            level = "强"
        elif formal_score >= 0.2:
            level = "中等"
        elif formal_score > -0.2:
            level = "中性"
        elif formal_score > -0.5:
            level = "弱"
        else:
            level = "差"

        return {
            "level": level,
            "trend_ok": trend_ok,
            "fundamental_ok": fundamental_ok,
            "formal_score": round(formal_score, 3),
            "note": "基于已完成日线与基本面，不代表今日盘中",
        }
