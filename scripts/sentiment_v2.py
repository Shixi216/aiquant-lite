"""综合情绪面 v2 命令行（影子策略 + 消融对比）

用法:
  python scripts/sentiment_v2.py 600509              # 深度研究（含影子策略v2）
  python scripts/sentiment_v2.py 600509 --screening  # 快速扫描（只读缓存）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))

# 影子策略 v2 权重
SHADOW_V2_WEIGHTS = {
    "技术面": 0.45, "基本面": 0.30, "情绪面": 0.15, "政策面": 0.05, "资金面": 0.05,
}
FORMAL_WEIGHTS = {"技术面": 0.60, "基本面": 0.40}  # 正式（保持不变）


def load_factors(symbol: str) -> dict:
    """读取五维基础分（技术/基本面/政策/资金），情绪面用 v2 计算"""
    import duckdb
    from config.settings import settings

    db_path = Path(settings.opc_database_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[1] / db_path
    con = duckdb.connect(str(db_path), read_only=True)

    from scripts.analyze_stock import (
        load_bars, load_financials, load_news, load_announcements,
        fundamental_factor, technical_factor, policy_factor,
    )
    from data_hub.services.capital_flow_enhanced import capital_flow_factor_enhanced

    bars = load_bars(con, symbol)
    tech = technical_factor(bars) if bars else {"score": 0.0, "confidence": 0.1}
    fin = load_financials(con, symbol)
    fund = fundamental_factor(fin)
    cap = capital_flow_factor_enhanced(symbol, bars or [])
    anns = load_announcements(con, symbol)
    policy = policy_factor(anns)
    con.close()
    return {
        "技术面": tech["score"], "基本面": fund["score"],
        "资金面": cap["score"], "政策面": policy["score"],
        "_conf": {"技术面": tech.get("confidence", 0.5), "基本面": fund.get("confidence", 0.5)},
    }


async def run(symbol: str, screening: bool = False) -> None:
    from trading.research.sentiment.sentiment_v2 import SentimentV2, STATUS_ZH

    start = time.perf_counter()
    engine = SentimentV2(symbol)
    mode = "快速扫描" if screening else "深度研究"
    result = await engine.compute(mode=mode)

    print("=" * 60)
    print(f"综合情绪面 v2 | {symbol} | 模式: {mode}")
    print("=" * 60)
    print(f"状态: {result.status}")
    print(f"数据截止: {result.data_cutoff[:19]}")
    print(f"缺失组件: {result.missing_components if result.missing_components else '无'}")
    print("-" * 60)
    print(f"事件情绪分: {result.event_sentiment_score:+.3f} (权重40%)")
    print(f"市场情绪分: {result.market_sentiment_score:+.3f} (权重25%)")
    print(f"板块情绪分: {result.sector_sentiment_score:+.3f} (权重25%)")
    print(f"关注度分:   {result.attention_score:+.3f} (权重10%)")
    print("-" * 60)
    print(f"覆盖率: {result.coverage_ratio:.0%} | 新鲜度: {result.freshness_ratio:.0%} | 平均置信度: {result.confidence:.2f}")
    print(f"最终情绪分: {result.final_sentiment_score:+.4f}")
    if result.detail:
        print(f"明细: {json.dumps(result.detail, ensure_ascii=False)}")
    # Tavily 补全信息（中文输出）
    d = result.detail
    print("-" * 60)
    print("Tavily 证据补全:")
    print(f"  触发: {'是' if d.get('tavily_triggered') else '否'}")
    if d.get("tavily_triggered"):
        print(f"  搜索请求数: {d.get('tavily_request_count', 0)} 次（上限2次）")
        print(f"  搜索结果数: {d.get('tavily_result_count', 0)} 条")
        print(f"  去重后结果: {d.get('deduplicated_result_count', 0)} 条")
        print(f"  官方来源数: {d.get('official_source_count', 0)} 条")
        print(f"  已验证事件: {d.get('verified_event_count', 0)} 条")
        print(f"  最新事件时间: {d.get('latest_event_time', '无')}")
        print(f"  来源质量分: {d.get('source_quality_score', 0.0)}")
        print(f"  缓存命中: {'是' if d.get('cache_hit') else '否'}")
        if d.get("degraded_reason"):
            print(f"  降级原因: {d.get('degraded_reason')}")
    print(f"耗时: {time.perf_counter()-start:.1f}秒")

    # 影子策略 v2（仅深度研究模式）
    if not screening and result.status not in ("数据不足", "已过期"):
        print()
        print("=" * 60)
        print("影子策略 v2（不改变正式决策）")
        print("=" * 60)
        factors = load_factors(symbol)
        # 情绪面实际权重 = 15% × 覆盖率 × 新鲜度 × 置信度（封顶20%）
        eff_weight = 0.15 * result.coverage_ratio * result.freshness_ratio * min(result.confidence, 1.0)
        eff_weight = min(0.20, eff_weight)

        formal = 0.6 * factors["技术面"] + 0.4 * factors["基本面"]
        shadow_v2 = (
            SHADOW_V2_WEIGHTS["技术面"] * factors["技术面"]
            + SHADOW_V2_WEIGHTS["基本面"] * factors["基本面"]
            + eff_weight * result.final_sentiment_score
            + SHADOW_V2_WEIGHTS["政策面"] * factors["政策面"]
            + SHADOW_V2_WEIGHTS["资金面"] * factors["资金面"]
        )
        # 消融：移除情绪面
        ablation = (
            SHADOW_V2_WEIGHTS["技术面"] * factors["技术面"]
            + SHADOW_V2_WEIGHTS["基本面"] * factors["基本面"]
            + SHADOW_V2_WEIGHTS["政策面"] * factors["政策面"]
            + SHADOW_V2_WEIGHTS["资金面"] * factors["资金面"]
        )

        print(f"正式 60/40 综合: {formal:+.4f}")
        print(f"影子策略v2 综合: {shadow_v2:+.4f}")
        print(f"消融(去情绪) 综合: {ablation:+.4f}")
        print(f"情绪面实际权重: {eff_weight:.1%} (封顶20%)")
        print(f"情绪面对影子分影响: {shadow_v2 - ablation:+.4f}")


def main():
    parser = argparse.ArgumentParser(description="综合情绪面v2")
    parser.add_argument("symbol", help="股票代码")
    parser.add_argument("--screening", action="store_true", help="快速扫描模式")
    args = parser.parse_args()
    asyncio.run(run(args.symbol, args.screening))


if __name__ == "__main__":
    main()
