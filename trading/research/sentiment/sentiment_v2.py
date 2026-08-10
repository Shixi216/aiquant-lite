"""综合情绪面 v2（事件情绪 + 市场情绪 + 板块情绪 + 关注度）

组成与权重：
1. 事件情绪 40%：新闻/公告/风险事件，Qwen批量提取，本地评分
2. 市场情绪 25%：涨跌家数、涨停跌停、炸板率、连板高度、成交额、波动率
3. 板块情绪 25%：板块涨跌、上涨占比、成交额变化、板块内涨停、个股相对强弱
4. 关注度 10%：新闻数量增速、事件关注度；无数据标 MISSING_DATA

动态质量调整：
最终情绪分 = 加权结果 × 覆盖率 × 新鲜度 × 平均置信度

边界：
- 龙虎榜/两融/主力资金 → 资金面，不进入
- 行业政策方向 → 政策面，不重复
- 新闻公告同事件 content_hash 去重
- AI只语义提取，本地规则评分
- SCREENING 只读缓存

状态：完整 / 部分 / 已过期 / 数据不足 / 降级兜底
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))

STATUS_ZH = {
    "COMPLETE": "完整",
    "PARTIAL": "部分",
    "STALE": "已过期",
    "INSUFFICIENT_DATA": "数据不足",
    "FALLBACK": "降级兜底",
}

# 事件类型中文化
EVENT_TYPE_ZH = {
    "EARNINGS_POSITIVE": "业绩利好", "EARNINGS_WARNING": "业绩预警",
    "DIVIDEND": "分红派息", "SHARE_REPURCHASE": "股份回购",
    "SHAREHOLDER_REDUCTION": "股东减持", "SHAREHOLDER_INCREASE": "股东增持",
    "CONTRACT_WIN": "重大合同", "MAJOR_LITIGATION": "重大诉讼",
    "REGULATORY_PENALTY": "监管处罚", "INVESTIGATION": "立案调查",
    "TRADING_SUSPENSION": "停牌", "TRADING_RESUMPTION": "复牌",
    "MANAGEMENT_CHANGE": "管理层变动", "PRODUCT_PRICE_CHANGE": "产品调价",
    "INDUSTRY_NEWS": "行业动态", "MARKET_RUMOR": "市场传闻", "OTHER": "其他事件",
}


@dataclass
class SentimentV2Result:
    event_sentiment_score: float = 0.0   # 事件情绪分
    market_sentiment_score: float = 0.0  # 市场情绪分
    sector_sentiment_score: float = 0.0  # 板块情绪分
    attention_score: float = 0.0         # 关注度分
    coverage_ratio: float = 0.0          # 覆盖率
    freshness_ratio: float = 0.0         # 新鲜度
    confidence: float = 0.0              # 平均置信度
    final_sentiment_score: float = 0.0   # 最终情绪分
    missing_components: list = field(default_factory=list)
    data_cutoff: str = ""
    status: str = "数据不足"
    detail: dict = field(default_factory=dict)


# 权重
W_EVENT = 0.40
W_MARKET = 0.25
W_SECTOR = 0.25
W_ATTENTION = 0.10


def _zh(v: float) -> str:
    return f"{v:+.3f}"


class SentimentV2:
    def __init__(self, symbol: str):
        self.symbol = symbol

    # ------------------------------------------------------------------
    # 1. 事件情绪（权重40%）— 含 Tavily 补全
    # ------------------------------------------------------------------
    async def _event_sentiment(self, cutoff: datetime) -> tuple[float, float, list[str], dict]:
        from trading.research.sentiment.batch_extractor import (
            BatchEventExtractor, build_bundles_from_records,
        )
        from trading.research.sentiment.tavily_enhancer import TavilyEnhancer
        from database.db import open_database
        from config.settings import settings

        db_path = Path(settings.opc_database_path)
        if not db_path.is_absolute():
            db_path = Path(__file__).resolve().parents[3] / db_path
        con = open_database(db_path, read_only=True)
        items = []
        for dtype in ("finance_news", "announcement"):
            try:
                rows = con.execute(
                    "SELECT payload_json FROM data_records WHERE symbol LIKE ? AND data_type=? "
                    "ORDER BY event_time DESC LIMIT 20",
                    [f"%{self.symbol}%", dtype],
                ).fetchall()
                for (payload,) in rows:
                    d = json.loads(payload) if isinstance(payload, str) else payload
                    inner = d.get("data", d)
                    items.append({
                        "title": d.get("title") or inner.get("title", ""),
                        "content": d.get("content") or inner.get("content", ""),
                        "publisher": d.get("publisher") or inner.get("publisher", ""),
                        "time": d.get("published_at") or inner.get("published_at")
                                or inner.get("announcement_date") or d.get("event_time"),
                        "url": d.get("url") or inner.get("url", ""),
                        "source_level": "official" if dtype == "announcement" else "media",
                    })
            except Exception:
                continue
        con.close()

        tavily_meta: dict = {
            "tavily_triggered": False, "tavily_request_count": 0,
            "tavily_result_count": 0, "deduplicated_result_count": 0,
            "official_source_count": 0, "verified_event_count": 0,
            "latest_event_time": "", "source_quality_score": 0.0,
            "cache_hit": False, "degraded_reason": "",
        }

        # Tavily 补全层：判断是否触发（仅深度研究模式）
        enhancer = TavilyEnhancer(self.symbol)
        enhance_result = enhancer.enhance(items)
        if enhance_result.tavily_triggered and enhance_result.events:
            # 将 Tavily 结果合并进 items（去重）
            existing_titles = {it.get("title", "") for it in items}
            for te in enhance_result.events:
                if te["title"] not in existing_titles:
                    items.append({
                        "title": te["title"], "content": te["content"],
                        "publisher": te["source"], "time": te["published_at"],
                        "url": te["url"], "source_level": te["source_level"],
                        "tavily": True,
                    })
            tavily_meta.update({
                "tavily_triggered": True,
                "tavily_request_count": enhance_result.tavily_request_count,
                "tavily_result_count": enhance_result.tavily_result_count,
                "deduplicated_result_count": enhance_result.deduplicated_result_count,
                "official_source_count": enhance_result.official_source_count,
                "verified_event_count": enhance_result.verified_event_count,
                "latest_event_time": enhance_result.latest_event_time,
                "source_quality_score": enhance_result.source_quality_score,
                "cache_hit": enhance_result.cache_hit,
                "degraded_reason": enhance_result.degraded_reason,
            })

        if not items:
            return 0.0, 0.3, ["事件数据不足"], tavily_meta

        # 去重：content_hash
        seen = set()
        deduped = []
        for it in items:
            h = abs(hash(it["title"] + it.get("publisher", "")))
            if h in seen:
                continue
            seen.add(h)
            deduped.append(it)

        # 只选最新、最相关的 5-8 条（控制 qwen 调用量，官方源优先）
        def _sort_key(it):
            official = 0 if it.get("source_level") == "official" else 1
            t = it.get("time")
            try:
                dt = datetime.fromisoformat(str(t).replace("Z", "+00:00")) if t else datetime.min.replace(tzinfo=TZ)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ)
            except Exception:
                dt = datetime.min.replace(tzinfo=TZ)
            return (official, -dt.timestamp())

        deduped.sort(key=_sort_key)
        deduped = deduped[:8]  # 最多8条进 qwen

        # 批量 Qwen 提取（单次批量请求）
        extractor = BatchEventExtractor(batch_size=8, max_workers=8, max_items=8)
        bundles = build_bundles_from_records(self.symbol, deduped, cutoff)
        result = await extractor.analyze(self.symbol, bundles, cutoff, mode="RESEARCH")
        score = result.score
        confidence = result.confidence if result.events else 0.3
        missing = [] if result.events else ["事件提取无结果"]
        return score, confidence, missing, tavily_meta

    # ------------------------------------------------------------------
    # 2. 市场整体情绪（权重25%）
    # ------------------------------------------------------------------
    def _market_sentiment(self) -> tuple[float, float, list[str]]:
        try:
            import tushare as ts
            from config.settings import settings
            ts.set_token(settings.tushare_token.strip())
            pro = ts.pro_api()
            end = datetime.now(tz=TZ).date()
            # 最新交易日全市场
            df = pro.daily(trade_date=end.strftime("%Y%m%d"))
            if df is None or df.empty:
                # 回退到最近交易日（统一日历服务）
                from data_hub.services.trading_calendar_service import TradingCalendarService
                last_open = TradingCalendarService().latest_trade_date(as_str=True)
                if not last_open:
                    return 0.0, 0.3, ["市场数据不足"]
                df = pro.daily(trade_date=last_open)
                if df is None or df.empty:
                    return 0.0, 0.3, ["市场数据不足"]

            n = len(df)
            up = (df["pct_chg"] > 0).sum()
            down = (df["pct_chg"] < 0).sum()
            limit_up = (df["pct_chg"] >= 9.8).sum()
            limit_down = (df["pct_chg"] <= -9.8).sum()
            total_amount = df["amount"].sum()

            # 涨跌家数比 → 市场宽度
            breadth = (up - down) / max(n, 1)
            # 涨停跌停比
            limit_ratio = (limit_up - limit_down) / max(n, 1)
            # 成交额（万亿）
            amount_t = total_amount / 1e6  # 千元→万亿约
            amount_score = min(0.3, max(-0.3, (amount_t - 1.0) * 0.1))

            score = breadth * 0.5 + limit_ratio * 0.3 + amount_score
            score = max(-1.0, min(1.0, score))
            confidence = 0.7 if n > 3000 else 0.5
            return score, confidence, []
        except Exception:
            return 0.0, 0.3, ["市场数据不可用"]

    # ------------------------------------------------------------------
    # 3. 板块情绪（权重25%）— stock_basic 行业分类聚合
    # ------------------------------------------------------------------
    def _sector_sentiment(self) -> tuple[float, float, list[str]]:
        try:
            import tushare as ts
            from config.settings import settings
            ts.set_token(settings.tushare_token.strip())
            pro = ts.pro_api()
            end = datetime.now(tz=TZ).date()

            # 获取该股行业
            basic = pro.stock_basic(fields="ts_code,name,industry")
            if basic is None or basic.empty:
                return 0.0, 0.3, ["板块数据不足"]
            # 匹配 ts_code 前缀（600509 → 600509.SH）
            row = basic[basic["ts_code"].str.startswith(self.symbol)]
            if row.empty:
                return 0.0, 0.3, ["未找到行业分类"]
            industry = row.iloc[0]["industry"]

            # 同行业成员
            members = basic[basic["industry"] == industry]["ts_code"].tolist()
            if not members:
                return 0.0, 0.3, ["行业无成员"]

            # 当日全市场（自动回退最近交易日）
            df = pro.daily(trade_date=end.strftime("%Y%m%d"))
            if df is None or df.empty:
                from data_hub.services.trading_calendar_service import TradingCalendarService
                last_open = TradingCalendarService().latest_trade_date(as_str=True)
                if not last_open:
                    return 0.0, 0.3, ["板块行情不足"]
                df = pro.daily(trade_date=last_open)
            if df is None or df.empty:
                return 0.0, 0.3, ["板块行情不足"]

            sector_df = df[df["ts_code"].isin(members)]
            if sector_df.empty:
                return 0.0, 0.3, ["板块行情不足"]

            n = len(sector_df)
            up = (sector_df["pct_chg"] > 0).sum()
            up_ratio = up / max(n, 1)
            avg_chg = sector_df["pct_chg"].mean()
            limit_up = (sector_df["pct_chg"] >= 9.8).sum()

            # 个股相对板块强弱
            stock_row = df[df["ts_code"].str.startswith(self.symbol)]
            stock_chg = stock_row.iloc[0]["pct_chg"] if not stock_row.empty else 0.0
            rel_strength = max(-0.5, min(0.5, (stock_chg - avg_chg) / 10))

            score = (up_ratio - 0.5) * 0.6 + avg_chg / 10 * 0.3 + rel_strength
            score = max(-1.0, min(1.0, score))
            confidence = 0.65 if n >= 10 else 0.4
            return score, confidence, []
        except Exception:
            return 0.0, 0.3, ["板块数据不可用"]

    # ------------------------------------------------------------------
    # 4. 关注度（权重10%）
    # ------------------------------------------------------------------
    def _attention_score(self) -> tuple[float, float, list[str]]:
        try:
            from database.db import open_database
            from config.settings import settings
            db_path = Path(settings.opc_database_path)
            if not db_path.is_absolute():
                db_path = Path(__file__).resolve().parents[3] / db_path
            con = open_database(db_path, read_only=True)
            rows = con.execute(
                "SELECT event_time FROM data_records WHERE symbol LIKE ? AND data_type='finance_news'",
                [f"%{self.symbol}%"],
            ).fetchall()
            con.close()
            if not rows:
                return 0.0, 0.3, ["关注度数据缺失"]

            # 新闻数量增速：近7天 vs 前7天
            now = datetime.now(tz=TZ)
            recent = sum(1 for (t,) in rows if t and t > now - timedelta(days=7))
            older = sum(1 for (t,) in rows if t and now - timedelta(days=14) <= t <= now - timedelta(days=7))
            growth = (recent - older) / max(older, 1)
            score = max(-0.5, min(0.5, growth * 0.3))
            confidence = 0.5
            return score, confidence, []
        except Exception:
            return 0.0, 0.3, ["关注度数据缺失"]

    # ------------------------------------------------------------------
    # 综合计算
    # ------------------------------------------------------------------
    async def compute(self, mode: str = "深度研究", data_cutoff: datetime | None = None) -> SentimentV2Result:
        cutoff = data_cutoff or datetime.now(tz=TZ)
        result = SentimentV2Result(data_cutoff=cutoff.isoformat())

        # 所有模式先查缓存：命中则直接返回（网络0、模型0、重复计算0）
        cached = self._load_cache(cutoff)
        if cached is not None:
            return cached

        # SCREENING：缓存缺失 → 已过期
        if mode == "快速扫描":
            result.status = "已过期"
            result.missing_components = ["缓存缺失"]
            return result

        # 各子因子
        ev_score, ev_conf, ev_missing, tavily_meta = await self._event_sentiment(cutoff)
        mk_score, mk_conf, mk_missing = self._market_sentiment()
        se_score, se_conf, se_missing = self._sector_sentiment()
        at_score, at_conf, at_missing = self._attention_score()

        # 覆盖率：有数据的子因子占比
        components = [
            (W_EVENT, ev_score, ev_conf, "事件情绪"),
            (W_MARKET, mk_score, mk_conf, "市场情绪"),
            (W_SECTOR, se_score, se_conf, "板块情绪"),
            (W_ATTENTION, at_score, at_conf, "关注度"),
        ]
        available = [(w, s, c, n) for w, s, c, n in components if not _is_missing(s)]
        coverage = sum(w for w, _, _, _ in available)
        result.coverage_ratio = round(coverage, 3)
        result.missing_components = [n for w, s, c, n in components if _is_missing(s)]

        if not available:
            result.status = "数据不足"
            result.final_sentiment_score = 0.0
            return result

        # 加权
        weighted = sum(s * w for w, s, _, _ in available) / sum(w for w, _, _, _ in available)
        result.event_sentiment_score = round(ev_score, 3)
        result.market_sentiment_score = round(mk_score, 3)
        result.sector_sentiment_score = round(se_score, 3)
        result.attention_score = round(at_score, 3)

        # 新鲜度：事件情绪的时间衰减
        freshness = 1.0
        if ev_missing == []:
            freshness = 0.9  # 有事件数据，较新
        result.freshness_ratio = round(freshness, 3)

        # 平均置信度
        avg_conf = sum(c for _, _, c, _ in available) / len(available)
        result.confidence = round(avg_conf, 3)

        # 最终分 = 加权 × 覆盖率 × 新鲜度 × 置信度
        final = weighted * coverage * freshness * avg_conf
        result.final_sentiment_score = round(max(-1.0, min(1.0, final)), 4)

        # 状态
        if coverage >= 0.9:
            result.status = "完整"
        elif coverage >= 0.5:
            result.status = "部分"
        else:
            result.status = "数据不足"

        result.detail = {
            "事件情绪": _zh(ev_score), "市场情绪": _zh(mk_score),
            "板块情绪": _zh(se_score), "关注度": _zh(at_score),
            "覆盖率": f"{coverage:.0%}", "新鲜度": f"{freshness:.0%}",
            "平均置信度": f"{avg_conf:.2f}",
            "tavily_triggered": tavily_meta.get("tavily_triggered", False),
            "tavily_request_count": tavily_meta.get("tavily_request_count", 0),
            "tavily_result_count": tavily_meta.get("tavily_result_count", 0),
            "deduplicated_result_count": tavily_meta.get("deduplicated_result_count", 0),
            "official_source_count": tavily_meta.get("official_source_count", 0),
            "verified_event_count": tavily_meta.get("verified_event_count", 0),
            "latest_event_time": tavily_meta.get("latest_event_time", ""),
            "source_quality_score": tavily_meta.get("source_quality_score", 0.0),
            "cache_hit": tavily_meta.get("cache_hit", False),
            "degraded_reason": tavily_meta.get("degraded_reason", ""),
        }
        self._save_cache(cutoff, result)
        return result

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
    def _cache_path(self, cutoff: datetime) -> Path:
        return Path(__file__).resolve().parents[3] / "cache" / "sentiment_v2" / \
            f"{self.symbol}_{cutoff.date().isoformat()}.json"

    def _load_cache(self, cutoff: datetime) -> SentimentV2Result | None:
        p = self._cache_path(cutoff)
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return SentimentV2Result(**d)
            except Exception:
                return None
        return None

    def _save_cache(self, cutoff: datetime, result: SentimentV2Result) -> None:
        p = self._cache_path(cutoff)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "event_sentiment_score": result.event_sentiment_score,
            "market_sentiment_score": result.market_sentiment_score,
            "sector_sentiment_score": result.sector_sentiment_score,
            "attention_score": result.attention_score,
            "coverage_ratio": result.coverage_ratio,
            "freshness_ratio": result.freshness_ratio,
            "confidence": result.confidence,
            "final_sentiment_score": result.final_sentiment_score,
            "missing_components": result.missing_components,
            "data_cutoff": result.data_cutoff,
            "status": result.status,
            "detail": result.detail,
        }, ensure_ascii=False), encoding="utf-8")


def _is_missing(score: float) -> bool:
    return abs(score) < 1e-9 and score == 0.0
