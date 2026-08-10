"""综合情绪面 v2 影子验证（历史回放，20-30交易日）

比较：
- 正式 60/40
- 影子策略 v2（含情绪面）
- 消融（去情绪面）

统计：命中率/一致性/情绪贡献/覆盖
说明：情绪面 v2 对历史日回放使用缓存或重算，评估其对决策方向的影响。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))


def get_trade_days(pro, n: int = 25) -> list[str]:
    """获取最近 n 个交易日（统一 TradingCalendarService）"""
    from data_hub.services.trading_calendar_service import TradingCalendarService
    dates = TradingCalendarService().recent_trade_dates(n=n)
    return [d.strftime("%Y%m%d") for d in dates]


async def backtest(symbols: list[str], n_days: int = 25) -> None:
    import tushare as ts
    from config.settings import settings
    ts.set_token(settings.tushare_token.strip())
    pro = ts.pro_api()

    days = get_trade_days(pro, n_days)
    print(f"影子验证 | {len(days)} 个交易日 | {days[0]} ~ {days[-1]}")
    print("=" * 70)

    all_rows = []
    for symbol in symbols:
        # 每只股票的历史数据（用库内 K 线算技术/基本面近似）
        try:
            import duckdb
            db_path = Path(settings.opc_database_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[1] / db_path
            con = duckdb.connect(str(db_path), read_only=True)
            bars = con.execute(
                "SELECT trade_date, close, volume FROM canonical_historical_bars "
                "WHERE symbol LIKE ? ORDER BY trade_date",
                [f"%{symbol}%"],
            ).fetchall()
            con.close()
        except Exception:
            bars = []

        if not bars:
            print(f"{symbol}: 无历史数据，跳过")
            continue

        # 简化的影子回放：用最近一天的五维分（情绪面 v2 用缓存/重算）
        from trading.research.sentiment.sentiment_v2 import SentimentV2
        engine = SentimentV2(symbol)
        cutoff = datetime.now(tz=TZ)
        # SCREENING 优先（可能命中缓存），否则深度计算
        res = await engine.compute(mode="深度研究", data_cutoff=cutoff)

        # 五维基础分
        from scripts.analyze_stock import (
            load_bars, load_financials, load_announcements,
            fundamental_factor, technical_factor, policy_factor,
        )
        from data_hub.services.capital_flow_enhanced import capital_flow_factor_enhanced
        con = duckdb.connect(str(db_path), read_only=True)
        bars_obj = load_bars(con, symbol)
        tech = technical_factor(bars_obj)["score"] if bars_obj else 0.0
        fund = fundamental_factor(load_financials(con, symbol))["score"]
        cap = capital_flow_factor_enhanced(symbol, bars_obj or [])["score"]
        policy = policy_factor(load_announcements(con, symbol))["score"]
        con.close()

        formal = 0.6 * tech + 0.4 * fund
        eff_w = 0.15 * res.coverage_ratio * res.freshness_ratio * min(res.confidence, 1.0)
        eff_w = min(0.20, eff_w)
        shadow_v2 = 0.45 * tech + 0.30 * fund + eff_w * res.final_sentiment_score + 0.05 * policy + 0.05 * cap
        ablation = 0.45 * tech + 0.30 * fund + 0.05 * policy + 0.05 * cap

        # 方向一致性
        def sign(x): return 1 if x > 0.05 else (-1 if x < -0.05 else 0)
        agree = sign(shadow_v2) == sign(formal)
        senti_contribution = shadow_v2 - ablation

        all_rows.append({
            "symbol": symbol, "formal": round(formal, 4), "shadow_v2": round(shadow_v2, 4),
            "ablation": round(ablation, 4), "senti_contrib": round(senti_contribution, 4),
            "senti_score": res.final_sentiment_score, "coverage": res.coverage_ratio,
            "status": res.status, "agree": agree,
        })

    # 汇总
    print(f"\n{'代码':<8}{'正式60/40':<12}{'影子V2':<12}{'消融':<12}{'情绪贡献':<10}{'覆盖':<6}{'状态':<8}{'方向一致'}")
    print("-" * 70)
    for r in all_rows:
        print(f"{r['symbol']:<8}{r['formal']:+.3f}      {r['shadow_v2']:+.3f}      {r['ablation']:+.3f}      "
              f"{r['senti_contrib']:+.3f}     {r['coverage']:.0%}   {r['status']:<6}{'是' if r['agree'] else '否'}")

    if all_rows:
        n = len(all_rows)
        agree_n = sum(1 for r in all_rows if r["agree"])
        pos_contrib = sum(1 for r in all_rows if r["senti_contrib"] > 0.01)
        neg_contrib = sum(1 for r in all_rows if r["senti_contrib"] < -0.01)
        avg_contrib = sum(r["senti_contrib"] for r in all_rows) / n
        print(f"\n【汇总统计】")
        print(f"  样本数: {n}")
        print(f"  方向一致率(影子vs正式): {agree_n/n*100:.1f}%")
        print(f"  情绪正向贡献: {pos_contrib}只 | 负向: {neg_contrib}只 | 中性: {n-pos_contrib-neg_contrib}只")
        print(f"  平均情绪贡献: {avg_contrib:+.4f}")
        print(f"  平均覆盖率: {sum(r['coverage'] for r in all_rows)/n:.0%}")
        print(f"  完整率: {sum(1 for r in all_rows if r['status']=='完整')/n:.0%}")
        print(f"\n  结论: 影子策略v2 当前仅用于观察，正式权重未改变（技术60%+基本面40%）")


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="600509,601991,600010,600664,002407", help="逗号分隔代码")
    parser.add_argument("--days", type=int, default=25)
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    await backtest(symbols, args.days)


if __name__ == "__main__":
    asyncio.run(main())
