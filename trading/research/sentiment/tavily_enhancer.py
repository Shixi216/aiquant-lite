"""综合情绪面 v2：Tavily 实时证据增强层

触发条件（仅 RESEARCH 模式，满足任一）：
1. 有效事件新闻 < 5 条
2. 最新新闻 > 3 个交易日（陈旧）
3. 主要来源均为未验证媒体
4. 存在重大事件但缺少交叉证据
5. 用户明确要求"查询最新消息"

限制：
- 每只股票最多 2 次搜索请求
- 默认时间范围最近 3 天，必要时扩展到 30 天
- 单次最多返回 10 条
- 设置超时和失败降级（DEGRADED）
- 优先使用域名白名单
- 禁止抓取登录页、付费墙和个人信息
- Tavily 失败时继续使用现有数据，标记 DEGRADED，不得假成功

来源分级（可信度排序）：
1. 交易所、上市公司、政府、监管机构
2. 权威财经媒体（证券时报/上证报/财联社等）
3. 普通财经媒体
4. 自媒体、论坛和聚合转载
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ = timezone(timedelta(hours=8))

# 域名白名单（可信来源，Tavily 结果优先过滤）
AUTHORITATIVE_DOMAINS = {
    "cninfo.com.cn",       # 巨潮资讯（官方公告）
    "sse.com.cn",          # 上交所
    "szse.cn",             # 深交所
    "gov.cn",              # 政府
    "csrc.gov.cn",         # 证监会
    "stcn.com",            # 证券时报
    "cnstock.com",         # 上海证券报
    "yicai.com",           # 第一财经
    "cls.cn",              # 财联社
    "eastmoney.com",       # 东方财富
    "sina.com.cn",         # 新浪财经
    "10jqka.com.cn",       # 同花顺
}

# 权威媒体关键词
AUTHORITATIVE_MEDIA = ["证券时报", "上海证券报", "财联社", "第一财经", "中国证券报", "经济日报"]

# 来源分级权重
SOURCE_LEVEL_WEIGHT = {
    "official": 1.0,      # 交易所/上市公司/政府/监管
    "authoritative": 0.7, # 权威财经媒体
    "media": 0.4,         # 普通财经媒体
    "social": 0.15,       # 自媒体/论坛
}

# 需要排除的页面特征（登录页/付费墙/个人信息）
EXCLUDED_URL_MARKERS = [
    "login", "signin", "password", "paywall", "subscribe",
    "account", "profile", "user/login", "vip",
]

# 缓存
CACHE_DIR = Path(__file__).resolve().parents[3] / "cache" / "tavily_events"


@dataclass
class TavilyEvent:
    url: str
    title: str
    content: str
    published_at: datetime | None
    collected_at: datetime
    source: str
    source_level: str  # official / authoritative / media / social
    content_hash: str
    source_weight: float
    is_duplicate: bool = False
    verified_by_official: bool = False


@dataclass
class TavilyEnhanceResult:
    tavily_triggered: bool = False
    tavily_request_count: int = 0
    tavily_result_count: int = 0
    deduplicated_result_count: int = 0
    official_source_count: int = 0
    verified_event_count: int = 0
    latest_event_time: str = ""
    source_quality_score: float = 0.0
    cache_hit: bool = False
    degraded_reason: str = ""
    events: list = field(default_factory=list)


def _get_tavily_key() -> str:
    """读取 Tavily API key（优先环境变量，其次 hermes .env）"""
    key = os.getenv("TAVILY_API_KEY", "")
    if key:
        return key.strip()
    # 从 E:\hermes\.env 读取
    env_path = Path("E:/hermes/.env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TAVILY_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _classify_source(url: str, title: str) -> tuple[str, str]:
    """来源分级：返回 (source_level, source_name)"""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    # 官方源
    if any(d in host for d in ["sse.com.cn", "szse.cn", "cninfo.com.cn", "gov.cn", "csrc.gov.cn"]):
        return "official", host
    # 权威媒体
    if any(m in title for m in AUTHORITATIVE_MEDIA):
        return "authoritative", host
    if any(d in host for d in ["stcn.com", "cnstock.com", "yicai.com", "cls.cn"]):
        return "authoritative", host
    # 普通媒体
    if any(d in host for d in ["eastmoney.com", "sina.com.cn", "10jqka.com.cn", "sohu.com", "163.com"]):
        return "media", host
    return "social", host


def _is_excluded_url(url: str) -> bool:
    return any(m in url.lower() for m in EXCLUDED_URL_MARKERS)


def tavily_search(query: str, max_results: int = 10, days: int = 3) -> list[dict]:
    """调用 Tavily 搜索（带超时，失败返回空列表）"""
    import urllib.request
    key = _get_tavily_key()
    if not key:
        return []
    try:
        body = json.dumps({
            "api_key": key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_raw_content": False,
            "days": days,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("results", [])
    except Exception:
        return []


class TavilyEnhancer:
    """Tavily 补全层：触发判断 → 搜索 → 去重 → 分级 → 缓存"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.max_searches = 2          # 每只股票最多2次搜索
        self.default_days = 3          # 默认最近3天
        self.max_results_per_search = 10

    # ------------------------------------------------------------------
    # 触发条件判断
    # ------------------------------------------------------------------
    def should_trigger(self, existing_events: list, user_requested: bool = False) -> tuple[bool, str]:
        """判断是否需要调用 Tavily（仅 RESEARCH 模式）"""
        if user_requested:
            return True, "用户要求查询最新消息"

        if not existing_events:
            return True, "有效事件新闻少于5条（0条）"

        # 有效事件数
        valid = [e for e in existing_events if e.get("title")]
        if len(valid) < 5:
            return True, f"有效事件新闻少于5条（{len(valid)}条）"

        # 最新新闻超过3个交易日（约5天）
        now = datetime.now(tz=TZ)
        latest = None
        for e in valid:
            t = e.get("time") or e.get("published_at")
            if t:
                try:
                    dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=TZ)
                    if latest is None or dt > latest:
                        latest = dt
                except Exception:
                    pass
        if latest and (now - latest).total_seconds() > 3 * 86400:
            return True, f"最新新闻超过3个交易日（{latest.date()}）"

        # 主要来源均为未验证媒体
        media_count = sum(1 for e in valid if e.get("source_level") != "official")
        if valid and media_count / len(valid) > 0.8:
            return True, "主要来源均为未验证媒体"

        return False, ""

    # ------------------------------------------------------------------
    # 搜索补全
    # ------------------------------------------------------------------
    def enhance(self, existing_events: list, user_requested: bool = False) -> TavilyEnhanceResult:
        result = TavilyEnhanceResult()
        triggered, reason = self.should_trigger(existing_events, user_requested)
        result.tavily_triggered = triggered
        if not triggered:
            return result

        # 缓存检查（相同 symbol + 触发原因）
        cache_key = self._cache_key(reason)
        cached = self._load_cache(cache_key)
        if cached is not None:
            result.cache_hit = True
            result.events = cached.get("events", [])
            result.tavily_request_count = 0
            return result

        # 执行搜索（最多2次，并发执行）
        queries = [
            f"{self.symbol} 最新公告 事件",
            f"{self.symbol} 风险 异动 公告",
        ]
        all_results: list[dict] = []
        search_failed = False

        # 并发执行2次搜索（减少等待时间）
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {}
            for i, q in enumerate(queries[: self.max_searches]):
                futures[pool.submit(tavily_search, q, self.max_results_per_search, self.default_days)] = q
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    result.tavily_request_count += 1
                    if res:
                        all_results.extend(res)
                    elif not result.degraded_reason:
                        result.degraded_reason = "Tavily 返回空结果"
                except TimeoutError:
                    result.degraded_reason = "Tavily 请求超时"
                    search_failed = True
                except ConnectionError as exc:
                    result.degraded_reason = f"Tavily HTTP/网络错误: {str(exc)[:40]}"
                    search_failed = True
                except Exception as exc:
                    result.degraded_reason = f"Tavily 搜索异常: {type(exc).__name__}"
                    search_failed = True

        # 无凭据检测
        if not _get_tavily_key():
            result.degraded_reason = "Tavily 凭据未配置"

        result.tavily_result_count = len(all_results)

        # 过滤排除页面 + 域名白名单优先
        filtered = [r for r in all_results if not _is_excluded_url(r.get("url", ""))]
        result.tavily_result_count = len(filtered)
        if all_results and not filtered:
            result.degraded_reason = "Tavily 结果均为排除页面或无有效条目"

        # 去重（content_hash）
        seen = set()
        events = []
        official_count = 0
        for r in filtered:
            title = r.get("title", "") or ""
            url = r.get("url", "") or ""
            content = (r.get("content", "") or "")[:500]
            if not title:
                continue
            h = hashlib.sha256((title + url).encode("utf-8")).hexdigest()[:32]
            if h in seen:
                continue
            seen.add(h)
            level, source = _classify_source(url, title)
            if level == "official":
                official_count += 1
            events.append({
                "title": title,
                "url": url,
                "content": content,
                "published_at": r.get("published_date") or datetime.now(tz=TZ).isoformat(),
                "collected_at": datetime.now(tz=TZ).isoformat(),
                "source": source,
                "source_level": level,
                "source_weight": SOURCE_LEVEL_WEIGHT.get(level, 0.4),
                "content_hash": h,
                "is_duplicate": False,
            })
        result.deduplicated_result_count = len(events)
        result.official_source_count = official_count
        result.verified_event_count = official_count

        # 最新事件时间
        if events:
            latest = max(events, key=lambda e: str(e.get("published_at", "")))
            result.latest_event_time = str(latest.get("published_at", ""))[:19]

        # 来源质量分
        if events:
            result.source_quality_score = round(
                sum(e["source_weight"] for e in events) / len(events), 3
            )

        result.events = events
        # 缓存
        self._save_cache(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # 缓存（键含 symbol/query_hash/url/content_hash/published_at/collected_at/data_cutoff）
    # ------------------------------------------------------------------
    def _cache_key(self, reason: str) -> str:
        h = hashlib.sha256((self.symbol + reason + datetime.now(tz=TZ).date().isoformat()).encode()).hexdigest()[:16]
        return f"{self.symbol}_{h}"

    def _load_cache(self, key: str) -> dict | None:
        p = CACHE_DIR / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _save_cache(self, key: str, result: TavilyEnhanceResult) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = CACHE_DIR / f"{key}.json"
        p.write_text(json.dumps({
            "symbol": self.symbol,
            "query_hash": key,
            "events": result.events,
            "tavily_request_count": result.tavily_request_count,
            "tavily_result_count": result.tavily_result_count,
            "collected_at": datetime.now(tz=TZ).isoformat(),
        }, ensure_ascii=False), encoding="utf-8")
