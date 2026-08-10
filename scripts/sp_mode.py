"""情绪面/政策面 三模式命令行（实盘投研级）

用法:
  python scripts/sp_mode.py 600010 --mode SCREENING   # 只读缓存，0请求
  python scripts/sp_mode.py 600010 --mode RESEARCH    # 批量AI提取（默认）
  python scripts/sp_mode.py 600010 --mode DECISION --cutoff 2026-07-31  # point-in-time

SCREENING: 网络请求0 模型调用0，缓存过期标记 STALE
RESEARCH:  缓存缺失/过期时批量调用 qwen，单股≤30秒
DECISION:  只用 data_cutoff 之前信息，缺失返回 INSUFFICIENT_DATA
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TZ = timezone(timedelta(hours=8))

# 状态统一（对外展示全部用中文）
STATUS_ZH = {
    "CONFIRMED": "已确认",
    "DEGRADED": "降级",
    "STALE": "已过期",
    "INSUFFICIENT_DATA": "数据不足",
    "FAILED": "失败",
    "FALLBACK": "降级兜底",
    "NOT_CALLED": "未调用",
    "INVALID": "无效",
    "VALID": "有效",
    "REPAIRED": "已修复",
}

# 事件类型中文化
EVENT_TYPE_ZH = {
    "EARNINGS_POSITIVE": "业绩利好",
    "EARNINGS_WARNING": "业绩预警",
    "DIVIDEND": "分红派息",
    "SHARE_REPURCHASE": "股份回购",
    "SHAREHOLDER_REDUCTION": "股东减持",
    "SHAREHOLDER_INCREASE": "股东增持",
    "CONTRACT_WIN": "重大合同",
    "MAJOR_LITIGATION": "重大诉讼",
    "REGULATORY_PENALTY": "监管处罚",
    "INVESTIGATION": "立案调查",
    "TRADING_SUSPENSION": "停牌",
    "TRADING_RESUMPTION": "复牌",
    "MANAGEMENT_CHANGE": "管理层变动",
    "PRODUCT_PRICE_CHANGE": "产品调价",
    "INDUSTRY_NEWS": "行业动态",
    "MARKET_RUMOR": "市场传闻",
    "OTHER": "其他事件",
}


def zh_status(s: str) -> str:
    return STATUS_ZH.get(s, s)


def zh_event_type(t: str) -> str:
    return EVENT_TYPE_ZH.get(t, t)


def load_records(symbol: str) -> list[dict]:
    """只读连接加载新闻+公告（市场查询用只读连接，短生命周期，用完即关）"""
    import duckdb
    from config.settings import settings
    import pathlib
    db_path = pathlib.Path(settings.opc_database_path)
    if not db_path.is_absolute():
        db_path = pathlib.Path(__file__).resolve().parents[1] / db_path
    items = []
    # 短生命周期连接：打开→查询→立即关闭，避免跨线程复用
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for dtype in ("finance_news", "announcement"):
            try:
                rows = con.execute(
                    "SELECT payload_json FROM data_records WHERE symbol LIKE ? AND data_type=? "
                    "ORDER BY event_time DESC LIMIT 20",
                    [f"%{symbol}%", dtype],
                ).fetchall()
                for (payload,) in rows:
                    d = json.loads(payload) if isinstance(payload, str) else payload
                    data = d.get("data", d)
                    pub = d.get("published_at") or data.get("announcement_date") or d.get("event_time")
                    items.append({
                        "title": data.get("title", d.get("title", "")),
                        "content": data.get("content", ""),
                        "publisher": data.get("publisher", d.get("source_name", "media")),
                        "time": pub,
                        "url": data.get("url", ""),
                        "source_level": "official" if dtype == "announcement" else "media",
                        "content_hash": d.get("content_hash", "h"),
                    })
            except Exception:
                continue
    finally:
        con.close()
    return items


async def run_mode(symbol: str, mode: str, cutoff: datetime) -> None:
    from trading.research.sentiment.batch_extractor import (
        BatchEventExtractor, build_bundles_from_records,
    )

    mode_zh = {"SCREENING": "快速扫描", "RESEARCH": "深度研究", "DECISION": "正式决策"}
    start = time.perf_counter()
    extractor = BatchEventExtractor()

    if mode == "SCREENING":
        res = await extractor.analyze(symbol, [], cutoff, mode="SCREENING")
        elapsed = (time.perf_counter() - start) * 1000
        print(f"[{mode_zh[mode]}] {symbol} | 状态: {zh_status(res.status)} | 耗时: {elapsed:.0f}毫秒")
        if res.status == "STALE":
            print("  缓存已过期或缺失 → 请使用深度研究模式更新")
        else:
            print(f"  得分: {res.score:+.3f} | 置信度: {res.confidence:.2f} | 缓存命中: {'是' if res.cache_hit else '否'}")
        return

    # RESEARCH / DECISION 需要真实数据
    items = load_records(symbol)
    if not items:
        print(f"[{mode_zh[mode]}] {symbol} | 状态: 数据不足（无新闻/公告记录）")
        return

    # DECISION: point-in-time 过滤（只保留 cutoff 之前的信息）
    if mode == "DECISION":
        before = [i for i in items if _parse_time(i.get("time")) <= cutoff]
        skipped = len(items) - len(before)
        items = before
        if skipped:
            print(f"[{mode_zh[mode]}] 时间点校验: 剔除 {skipped} 条截止时间之后的信息")

    bundles = build_bundles_from_records(symbol, items, cutoff)
    res = await extractor.analyze(symbol, bundles, cutoff, mode="RESEARCH")
    elapsed = (time.perf_counter() - start) * 1000

    print(f"[{mode_zh[mode]}] {symbol} | 状态: {zh_status(res.status)} | 耗时: {elapsed:.0f}毫秒")
    print(f"  得分: {res.score:+.3f} | 置信度: {res.confidence:.2f} | 事件: {len(res.events)}条 | 模型: {res.model_used or '无'}")
    print(f"  缓存: {'命中' if res.cache_hit else '新生成'}")
    if res.events:
        print("  事件明细:")
        for ev in res.events[:8]:
            print(f"    [{zh_event_type(ev['event_type'])}] 方向={ev['direction']:+d} "
                  f"强度={ev['intensity']:.2f} 得分={ev['score']:+.3f} "
                  f"新鲜度={ev['freshness']:.2f} 模型={ev['model']}")

    # DECISION 模式：缺失/过期 → 数据不足
    if mode == "DECISION" and res.status == "INSUFFICIENT_DATA":
        print("  → 数据不足，不得伪造完整五维结果")


def _parse_time(v) -> datetime:
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=TZ)
        return v
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if d.tzinfo is None:
                return d.replace(tzinfo=TZ)
            return d
        except ValueError:
            pass
    return datetime.now(tz=TZ) - timedelta(days=9999)


def main():
    parser = argparse.ArgumentParser(description="情绪/政策面三模式")
    parser.add_argument("symbol", help="股票代码")
    parser.add_argument("--mode", choices=["SCREENING", "RESEARCH", "DECISION"], default="RESEARCH")
    parser.add_argument("--cutoff", default=None, help="DECISION 模式数据截止日 YYYY-MM-DD")
    args = parser.parse_args()

    cutoff = datetime.now(tz=TZ)
    if args.cutoff:
        cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d").replace(tzinfo=TZ)

    asyncio.run(run_mode(args.symbol, args.mode, cutoff))


if __name__ == "__main__":
    main()
