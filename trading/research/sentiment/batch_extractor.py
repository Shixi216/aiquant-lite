"""情绪面/政策面 实盘投研级升级引擎（SHADOW_VALIDATION）

核心设计：
- 批量调用 Qwen（每批 5-10 条，asyncio.gather 并发），禁止逐条串行
- 模型只提取结构化事件，最终得分由本地确定性规则计算
- 缓存 + 三模式（SCREENING 0请求 / RESEARCH 批量 / DECISION point-in-time）
- 降级链：AI → 关键词规则，状态标记 CONFIRMED/DEGRADED/STALE/INSUFFICIENT_DATA/FAILED
- 正式权重保持 0（shadow_mode=True），不改变技术面60%+基本面40%
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

TZ = timezone(timedelta(hours=8))

# 状态统一
CONFIRMED = "CONFIRMED"
DEGRADED = "DEGRADED"
STALE = "STALE"
INSUFFICIENT = "INSUFFICIENT_DATA"
FAILED = "FAILED"
FALLBACK = "FALLBACK"

# 信息源权重（公告 > 政策 > 权威媒体 > 普通新闻）
SOURCE_WEIGHTS = {
    "official_announcement": 1.0,   # 交易所/公司公告
    "official_policy": 1.0,         # 国务院/部委/监管/地方政府文件
    "authoritative_media": 0.7,     # 权威媒体（证券时报/上证报等）
    "media": 0.4,                   # 普通媒体
    "social": 0.15,                 # 传闻/社交
}


@dataclass
class ExtractedEvent:
    event_id: str
    symbol: str
    event_type: str
    direction: int  # 1 正 / -1 负 / 0 中性
    intensity: float  # 0-1
    confidence: float  # 0-1
    source_level: str
    source_weight: float
    published_at: datetime
    collected_at: datetime
    effective_until: datetime | None
    model: str
    content_hash: str
    data_cutoff: datetime
    scoring_version: str
    fallback_status: str
    evidence: str = ""
    uncertainty: float = 0.0
    is_duplicate: bool = False
    price_reflected: bool = False  # 是否已被市场价格反映


@dataclass
class SymbolScore:
    symbol: str
    score: float
    confidence: float
    status: str
    events: list[dict] = field(default_factory=list)
    model_used: str | None = None
    latency_ms: int = 0
    cache_hit: bool = False


class BatchEventExtractor:
    """批量事件提取 + 本地评分引擎"""

    def __init__(self, batch_size: int = 10, max_workers: int = 8, max_items: int = 20):
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.max_items = max_items
        self.scoring_version = "sp_v1"

    # ------------------------------------------------------------------
    # 批量调用（禁止串行）
    # ------------------------------------------------------------------
    async def _extract_batch(self, bundles: list) -> list[ExtractedEvent | None]:
        """一批 bundles 并发调用 qwen（asyncio.gather 纯协程并发，不用线程池）"""
        from trading.research.sentiment.event_extractor import SentimentEventExtractor

        extractor = SentimentEventExtractor()
        sem = asyncio.Semaphore(self.max_workers)

        async def one(bundle):
            async with sem:
                try:
                    result = await extractor.extract(bundle, allow_model_calls=True)
                    ext = result.extraction
                    if ext and result.validation_status.name in ("VALID", "REPAIRED"):
                        return self._to_event(bundle, ext, result)
                except Exception:
                    pass
                return None

        # asyncio.gather 纯协程并发（Windows 上避免 ThreadPoolExecutor 导致的 GIL 崩溃）
        return await asyncio.gather(*[one(b) for b in bundles])

    def _to_event(self, bundle, ext, result) -> ExtractedEvent:
        payload = bundle.primary_source.payload
        pub = bundle.event_time
        now = datetime.now(tz=TZ)
        content_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:32]
        # 有效期：按 impact_horizon
        horizon_days = {
            "INTRADAY": 0.5, "1D": 1, "1_5D": 5, "1_4W": 28, "LONG_TERM": 180,
        }.get(getattr(ext, "impact_horizon", "1_5D"), 5)
        return ExtractedEvent(
            event_id=f"evt_{content_hash[:16]}",
            symbol=bundle.symbols[0] if bundle.symbols else "",
            event_type=ext.event_type.value,
            direction=int(ext.direction),
            intensity=float(ext.intensity),
            confidence=float(ext.confidence),
            source_level=bundle.primary_source.source_level,
            source_weight=self._source_weight(bundle),
            published_at=pub,
            collected_at=now,
            effective_until=now + timedelta(days=horizon_days),
            model=getattr(result, "provider", "qwen") or "qwen",
            content_hash=content_hash,
            data_cutoff=bundle.data_cutoff,
            scoring_version=self.scoring_version,
            fallback_status=CONFIRMED,
            evidence=ext.summary,
            uncertainty=1.0 - float(ext.confidence),
        )

    def _source_weight(self, bundle) -> float:
        level = bundle.primary_source.source_level
        if level == "official":
            return SOURCE_WEIGHTS["official_announcement"]
        if level == "policy":
            return SOURCE_WEIGHTS["official_policy"]
        return SOURCE_WEIGHTS.get("media", 0.4)

    # ------------------------------------------------------------------
    # 本地确定性评分
    # ------------------------------------------------------------------
    def score_events(self, symbol: str, events: list[ExtractedEvent], data_cutoff: datetime) -> SymbolScore:
        if not events:
            return SymbolScore(symbol=symbol, score=0.0, confidence=0.0,
                               status=INSUFFICIENT, events=[])

        now = data_cutoff
        total = 0.0
        weight_sum = 0.0
        seen_hashes: set[str] = set()
        event_dicts: list[dict] = []

        # 去重：同一内容哈希只算一次（重复事件折扣）
        deduped = []
        for e in events:
            if e.content_hash in seen_hashes:
                continue
            seen_hashes.add(e.content_hash)
            deduped.append(e)

        # 本地规则强制修正：风险提示/异常波动类标题，AI 可能判中性，
        # 市场惯例视为利空，强制 direction=-1（本地确定性规则覆盖模型输出）
        for e in deduped:
            if e.event_type == "OTHER" and e.direction == 0:
                if any(k in e.evidence for k in ["异常波动", "风险提示", "风险"]):
                    e.direction = -1
                    e.fallback_status = DEGRADED

        for e in deduped:
            # 时间衰减：72h 半衰期（兼容 naive/aware）
            pub = e.published_at
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=TZ)
            age_hours = (now - pub).total_seconds() / 3600
            freshness = 0.5 ** (age_hours / 72) if age_hours > 0 else 1.0

            # 来源可信度
            source_w = e.source_weight

            # 强度 × 置信度
            strength = e.intensity * e.confidence

            # 已被市场反映（价格已包含）→ 折扣
            reflected_discount = 0.5 if e.price_reflected else 1.0

            # 单事件得分 = 方向 × 强度 × 置信 × 来源权重 × 新鲜度 × 反映折扣
            event_score = e.direction * strength * source_w * freshness * reflected_discount

            total += event_score * source_w
            weight_sum += source_w * freshness
            event_dicts.append({
                "event_id": e.event_id,
                "event_type": e.event_type,
                "direction": e.direction,
                "intensity": e.intensity,
                "confidence": e.confidence,
                "source_weight": source_w,
                "freshness": round(freshness, 3),
                "score": round(event_score, 4),
                "published_at": e.published_at.isoformat(),
                "model": e.model,
                "fallback_status": e.fallback_status,
            })

        # 归一化得分（有事件时）
        score = total / max(weight_sum, 1e-9) if weight_sum else 0.0
        score = max(-1.0, min(1.0, score))

        # 置信度：基于事件数、来源质量
        conf = min(0.9, 0.3 + 0.1 * len(deduped) + 0.2 * (weight_sum / max(len(deduped), 1)))

        status = CONFIRMED if all(e.fallback_status == CONFIRMED for e in deduped) else DEGRADED
        return SymbolScore(symbol=symbol, score=score, confidence=conf,
                           status=status, events=event_dicts)

    # ------------------------------------------------------------------
    # 缓存（内存 + 简表）
    # ------------------------------------------------------------------
    def _cache_key(self, symbol: str, cutoff: datetime) -> str:
        return f"{symbol}|{cutoff.date().isoformat()}"

    async def analyze(self, symbol: str, bundles: list, data_cutoff: datetime,
                      mode: str = "RESEARCH") -> SymbolScore:
        """三模式入口"""
        start = time.perf_counter()

        # SCREENING：只读缓存，0 模型调用
        if mode == "SCREENING":
            cached = self._load_cache(symbol, data_cutoff)
            if cached:
                cached.latency_ms = int((time.perf_counter() - start) * 1000)
                cached.cache_hit = True
                return cached
            return SymbolScore(symbol=symbol, score=0.0, confidence=0.0,
                               status=STALE, events=[], cache_hit=False)

        # RESEARCH：批量调用（禁止串行）
        if not bundles:
            return SymbolScore(symbol=symbol, score=0.0, confidence=0.0,
                               status=INSUFFICIENT, events=[])

        # 限制条数（控制耗时，单股≤30秒目标）
        bundles = bundles[: self.max_items]

        # 分批并发
        all_events: list[ExtractedEvent] = []
        for i in range(0, len(bundles), self.batch_size):
            batch = bundles[i:i + self.batch_size]
            results = await self._extract_batch(batch)
            all_events.extend([r for r in results if r is not None])

        result = self.score_events(symbol, all_events, data_cutoff)
        result.latency_ms = int((time.perf_counter() - start) * 1000)
        result.model_used = "qwen"
        self._save_cache(symbol, data_cutoff, result)
        return result

    # ------------------------------------------------------------------
    # 简单缓存（内存 + JSON 落盘）
    # ------------------------------------------------------------------
    def _load_cache(self, symbol: str, cutoff: datetime) -> SymbolScore | None:
        import pathlib
        cache_dir = pathlib.Path(__file__).resolve().parents[2] / "cache" / "sentiment"
        f = cache_dir / f"{symbol}_{cutoff.date().isoformat()}.json"
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                return SymbolScore(symbol=symbol, score=d["score"], confidence=d["confidence"],
                                   status=d["status"], events=d["events"],
                                   model_used=d.get("model"), cache_hit=True)
            except Exception:
                return None
        return None

    def _save_cache(self, symbol: str, cutoff: datetime, result: SymbolScore) -> None:
        import pathlib
        cache_dir = pathlib.Path(__file__).resolve().parents[2] / "cache" / "sentiment"
        cache_dir.mkdir(parents=True, exist_ok=True)
        f = cache_dir / f"{symbol}_{cutoff.date().isoformat()}.json"
        f.write_text(json.dumps({
            "score": result.score, "confidence": result.confidence,
            "status": result.status, "events": result.events,
            "model": result.model_used, "scoring_version": self.scoring_version,
        }, ensure_ascii=False), encoding="utf-8")


def build_bundles_from_records(symbol: str, news_items: list[dict],
                               data_cutoff: datetime) -> list:
    """从 data_records 的新闻/公告记录构建 EventBundle 列表"""
    from trading.research.sentiment.models import EventBundle, EventSourceRecord

    bundles = []
    for item in news_items:
        title = item.get("title", "") or ""
        content = item.get("content", "") or ""
        pub = item.get("time") or item.get("published_at") or datetime.now(tz=TZ)
        if isinstance(pub, str):
            try:
                pub = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                pub = datetime.now(tz=TZ)
        payload = {"title": title, "content": content, "publisher": item.get("publisher", "")}
        cid = f"evt_{abs(hash(title)) % 10**8}"
        src = EventSourceRecord(
            record_id=cid, event_time=pub, fetched_at=datetime.now(tz=TZ),
            source_name=item.get("publisher", "media"),
            source_url=item.get("url", ""), source_level=item.get("source_level", "media"),
            verified=False, content_hash=item.get("content_hash", "h"), payload=payload,
        )
        bundles.append(EventBundle(
            event_cluster_id=cid, canonical_title=title, cluster_event_type="UNKNOWN",
            event_time=pub, data_cutoff=data_cutoff, primary_source_id=cid,
            source_count=1, source_records=[src], symbols=[symbol], sectors=[],
            dedup_method="single", dedup_version="v1", cluster_hash=item.get("content_hash", "ch"),
        ))
    return bundles
