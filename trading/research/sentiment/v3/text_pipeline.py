from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import date, datetime, time
from statistics import median
from time import perf_counter
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from router.services.provider_registry import get_model_provider
from trading.research.sentiment.v3.calculators import SnapshotBuildContext, _meta
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.schemas import QualityFlag, SCHEMA_VERSION, SnapshotType
from trading.research.sentiment.v3.text_models import (
    CANONICAL_EVENT_VERSION,
    EVENT_ALGORITHM_VERSION,
    NEWS_SNAPSHOT_ALGORITHM_VERSION,
    TEXT_ALGORITHM_VERSION,
    TEXT_PROMPT_VERSION,
    EventIntelligenceRecord,
    EventIntelligenceSnapshot,
    EventSession,
    EventType,
    ExtractionMethod,
    FactStatus,
    LLMCallStats,
    NewsSentimentRecord,
    NewsSentimentSnapshot,
    Novelty,
    TextAnalysisRecord,
    TextDirection,
    TextDocument,
    TextExtractionOutput,
    TextHorizon,
    TextSourceQuality,
    TextType,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEWS_TYPES = {
    TextType.OPINION,
    TextType.EXPECTATION_CHANGE,
    TextType.MARKET_REACTION,
    TextType.RUMOR,
}
RISK_EVENTS = {
    EventType.LITIGATION,
    EventType.PENALTY,
    EventType.REGULATORY,
    EventType.INVESTIGATION,
    EventType.ST_STATUS,
    EventType.DELISTING_RISK,
}


class AnalysisCache(Protocol):
    def get_news_analysis_by_hash(self, *, cache_key: str) -> TextAnalysisRecord | None: ...
    def save_news_analysis(self, analysis: TextAnalysisRecord) -> TextAnalysisRecord: ...


class ExtractionClient(Protocol):
    provider: str
    model_name: str

    async def extract(
        self, document: TextDocument
    ) -> tuple[TextExtractionOutput, dict[str, Any]]: ...


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def document_content_hash(
    *, title: str, body: str, source: str, published_time: datetime | None
) -> str:
    value = {
        "title": _norm(title),
        "body": _norm(body),
        "source": _norm(source).lower(),
        "published_time": None if published_time is None else published_time.isoformat(),
    }
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def analysis_cache_key(
    *,
    content_hash: str,
    provider: str,
    model_name: str,
    prompt_version: str = TEXT_PROMPT_VERSION,
    schema_version: str = SCHEMA_VERSION,
    algorithm_version: str = TEXT_ALGORITHM_VERSION,
) -> str:
    value = {
        "content_hash": content_hash,
        "provider": provider,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "algorithm_version": algorithm_version,
    }
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def source_quality(source: str, *, is_announcement: bool) -> TextSourceQuality:
    value = source.lower()
    if is_announcement or any(x in value for x in ("cninfo", "巨潮", "交易所公告")):
        return TextSourceQuality.OFFICIAL_DISCLOSURE
    if any(x in value for x in ("证监会", "监管", "sse", "szse")):
        return TextSourceQuality.OFFICIAL_REGULATOR
    if any(x in value for x in ("公司官网", "company")):
        return TextSourceQuality.COMPANY_SOURCE
    if any(x in value for x in ("eastmoney", "东方财富", "证券时报", "新华社")):
        return TextSourceQuality.MAINSTREAM_FINANCIAL_MEDIA
    return TextSourceQuality.UNKNOWN


def build_text_document(
    *,
    document_id: str,
    symbol: str,
    title: str,
    body: str,
    source: str,
    ingested_time: datetime,
    published_time: datetime | None = None,
    event_time: datetime | None = None,
    source_url: str | None = None,
    is_announcement: bool = False,
    date_only: date | None = None,
) -> TextDocument:
    if ingested_time.tzinfo is None:
        raise ValueError("ingested_time must include timezone")
    flags: list[QualityFlag] = []
    effective = published_time
    if date_only is not None and effective is None:
        effective = min(datetime.combine(date_only, time(18), tzinfo=SHANGHAI), ingested_time)
        flags.append(QualityFlag.AVAILABLE_TIME_INFERRED)
    if effective is None:
        available = ingested_time
        flags.append(QualityFlag.PUBLISHED_TIME_UNKNOWN)
    else:
        available = min(effective, ingested_time)
    digest = document_content_hash(title=title, body=body, source=source, published_time=effective)
    return TextDocument(
        document_id=document_id,
        symbol=symbol,
        title=title,
        body=body,
        source=source,
        source_url=source_url,
        is_announcement=is_announcement,
        event_time=event_time,
        published_time=effective,
        available_time=available,
        ingested_time=ingested_time,
        source_quality=source_quality(source, is_announcement=is_announcement),
        content_hash=digest,
        quality_flags=flags,
    )


def classify_event_session(
    value: datetime | None, trading_days: set[date] | None = None
) -> EventSession:
    if value is None:
        return EventSession.UNKNOWN
    local = value.astimezone(SHANGHAI)
    trading = local.weekday() < 5 if trading_days is None else local.date() in trading_days
    if not trading:
        return EventSession.NON_TRADING_DAY
    if local.time() < time(9, 30):
        return EventSession.PRE_MARKET
    if local.time() <= time(15):
        return EventSession.IN_SESSION
    return EventSession.POST_MARKET


RULES = (
    (EventType.EARNINGS_FORECAST, TextDirection.NEUTRAL, ("业绩预告", "业绩快报")),
    (EventType.BUYBACK, TextDirection.POSITIVE, ("回购",)),
    (EventType.SHAREHOLDER_INCREASE, TextDirection.POSITIVE, ("增持",)),
    (EventType.SHAREHOLDER_REDUCTION, TextDirection.NEGATIVE, ("减持",)),
    (EventType.PENALTY, TextDirection.NEGATIVE, ("处罚", "罚款")),
    (EventType.INVESTIGATION, TextDirection.NEGATIVE, ("立案", "调查")),
    (EventType.LITIGATION, TextDirection.NEGATIVE, ("诉讼", "仲裁")),
    (EventType.DELISTING_RISK, TextDirection.NEGATIVE, ("退市风险", "终止上市")),
    (EventType.ST_STATUS, TextDirection.NEGATIVE, ("*st", "被实施st", "风险警示")),
    (EventType.REGULATORY, TextDirection.NEGATIVE, ("监管函", "问询函")),
    (EventType.EARNINGS, TextDirection.NEUTRAL, ("净利润", "营业收入", "年报", "季报")),
    (EventType.DIVIDEND, TextDirection.POSITIVE, ("分红", "派息")),
    (EventType.CONTRACT, TextDirection.POSITIVE, ("重大合同", "签订合同")),
    (EventType.ORDER, TextDirection.POSITIVE, ("中标", "订单")),
    (EventType.M_AND_A, TextDirection.NEUTRAL, ("并购", "收购")),
    (EventType.ASSET_RESTRUCTURING, TextDirection.NEUTRAL, ("资产重组", "重大资产")),
    (EventType.FINANCING, TextDirection.NEUTRAL, ("定增", "融资")),
    (EventType.EQUITY_INCENTIVE, TextDirection.NEUTRAL, ("股权激励",)),
    (EventType.MANAGEMENT_CHANGE, TextDirection.NEUTRAL, ("董事长辞职", "高管变动")),
    (EventType.MAJOR_SHAREHOLDER_CHANGE, TextDirection.NEUTRAL, ("控股股东变更",)),
    (EventType.PRODUCT, TextDirection.NEUTRAL, ("新产品", "获批上市")),
    (EventType.CAPACITY, TextDirection.NEUTRAL, ("产能", "投产")),
    (EventType.POLICY, TextDirection.NEUTRAL, ("政策", "国务院", "部委")),
)


def _evidence(text: str, keyword: str) -> str:
    index = text.find(keyword)
    return _norm(text[max(0, index - 50) : index + len(keyword) + 80])[:240]


def rule_fallback(document: TextDocument) -> TextExtractionOutput:
    text = f"{document.title} {document.body}".lower()
    event_type, direction, matched = EventType.OTHER, TextDirection.UNKNOWN, ""
    for candidate, candidate_direction, keywords in RULES:
        matched = next((word for word in keywords if word in text), "")
        if matched:
            event_type, direction = candidate, candidate_direction
            break
    if any(word in text for word in ("传闻", "据传", "未经证实")):
        text_type, status = TextType.RUMOR, FactStatus.RUMOR
    elif event_type in RISK_EVENTS:
        text_type, status = TextType.RISK, FactStatus.REPORTED
    elif event_type != EventType.OTHER:
        text_type = TextType.FACT_EVENT
        status = FactStatus.CONFIRMED if document.is_announcement else FactStatus.REPORTED
    elif any(word in text for word in ("预计", "预期", "上调", "下调")):
        text_type, status = TextType.EXPECTATION_CHANGE, FactStatus.REPORTED
    elif any(word in text for word in ("认为", "观点", "看好", "看淡")):
        text_type, status = TextType.OPINION, FactStatus.REPORTED
    elif any(word in text for word in ("股价", "市场反应", "高开", "低开")):
        text_type, status = TextType.MARKET_REACTION, FactStatus.REPORTED
    else:
        text_type, status = TextType.OTHER, FactStatus.UNKNOWN
    surprise = basis = None
    for value, keywords in (
        (TextDirection.POSITIVE, ("超预期", "超过预期")),
        (TextDirection.NEGATIVE, ("不及预期", "低于预期")),
    ):
        keyword = next((word for word in keywords if word in text), "")
        if keyword:
            surprise, basis = value, _evidence(text, keyword)
            break
    return TextExtractionOutput(
        text_type=text_type,
        event_type=event_type,
        direction=direction,
        intensity=0.45 if matched else 0.1,
        confidence=0.55 if matched else 0.2,
        horizon=TextHorizon.UNKNOWN,
        novelty=Novelty.NEW,
        surprise=surprise,
        surprise_basis=basis,
        event_time=document.event_time,
        available_time=document.available_time,
        source_quality=document.source_quality,
        fact_status=status,
        evidence=[_evidence(text, matched)] if matched else [],
    )


class QwenExtractionClient:
    provider = "qwen"

    def __init__(
        self, *, model_name: str = "qwen", provider_factory: Callable = get_model_provider
    ) -> None:
        self.model_name, self.provider_factory = model_name, provider_factory

    async def extract(self, document: TextDocument) -> tuple[TextExtractionOutput, dict[str, Any]]:
        provider = self.provider_factory(self.provider)
        if provider is None:
            raise RuntimeError("qwen provider unavailable")
        prompt = json.dumps(
            {
                "prompt_version": TEXT_PROMPT_VERSION,
                "rules": [
                    "Text understanding only; do not calculate market facts.",
                    "surprise must be null without an explicit expectation basis.",
                ],
                "schema": TextExtractionOutput.model_json_schema(),
                "document": {
                    "title": document.title,
                    "body": document.body[:12000],
                    "source": document.source,
                    "symbol": document.symbol,
                    "available_time": document.available_time.isoformat(),
                    "source_quality": document.source_quality.value,
                },
            },
            ensure_ascii=False,
        )
        started = perf_counter()
        response = await provider.invoke(
            role="sentiment_v3_text_extractor",
            prompt=prompt,
            system_prompt="Return strict JSON only. No trading advice.",
            temperature=0.0,
            max_tokens=1200,
        )
        content = response.content.strip()
        if content.startswith("```"):
            content = "\n".join(content.splitlines()[1:-1]).strip()
        output = TextExtractionOutput.model_validate(json.loads(content)).model_copy(
            update={
                "available_time": document.available_time,
                "source_quality": document.source_quality,
            }
        )
        source_text = _norm(f"{document.title} {document.body}").lower()
        explicit_basis = any(
            word in source_text for word in ("预期", "指引", "consensus", "forecast")
        )
        if output.surprise is not None and not explicit_basis:
            output = output.model_copy(update={"surprise": None, "surprise_basis": None})
        usage = getattr(response, "usage", {}) or {}
        return output, {
            "latency_ms": float(getattr(response, "latency_ms", (perf_counter() - started) * 1000)),
            "model_name": str(getattr(response, "model", self.model_name)),
            "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
            "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
        }


def canonical_event_id(document: TextDocument, output: TextExtractionOutput) -> str:
    text = _norm(f"{document.title} {document.body}").lower()
    numbers = sorted(set(re.findall(r"\d+(?:\.\d+)?(?:亿|万|%|元)?", text)))[:8]
    event_day = (
        (output.event_time or document.event_time or document.available_time)
        .astimezone(SHANGHAI)
        .date()
    )
    value = {
        "version": CANONICAL_EVENT_VERSION,
        "symbol": document.symbol,
        "event_type": output.event_type.value,
        "event_day": str(event_day),
        "numeric_facts": numbers,
    }
    return "cev_" + hashlib.sha256(canonical_json(value).encode()).hexdigest()[:32]


class TextIntelligenceProcessor:
    def __init__(
        self,
        *,
        cache: AnalysisCache,
        client: ExtractionClient | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.cache, self.client = cache, client or QwenExtractionClient()
        self.max_concurrency = max(1, max_concurrency)

    async def process(self, documents: Iterable[TextDocument]):
        started = perf_counter()
        unique = {}
        seen = 0
        for document in documents:
            seen += 1
            unique.setdefault(document.content_hash, document)
        hash_ms = (perf_counter() - started) * 1000
        hits = misses = calls = fallback = failures = 0
        lookup_ms = qwen_ms = 0.0
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def one(document: TextDocument) -> TextAnalysisRecord:
            nonlocal hits, misses, calls, fallback, failures, lookup_ms, qwen_ms
            key = analysis_cache_key(
                content_hash=document.content_hash,
                provider=self.client.provider,
                model_name=self.client.model_name,
            )
            tick = perf_counter()
            cached = self.cache.get_news_analysis_by_hash(cache_key=key)
            lookup_ms += (perf_counter() - tick) * 1000
            if cached is not None:
                hits += 1
                return cached
            misses += 1
            flags = list(document.quality_flags)
            metadata = {}
            try:
                async with semaphore:
                    calls += 1
                    output, metadata = await self.client.extract(document)
                method = ExtractionMethod.STRUCTURED_AI
                latency = float(metadata.get("latency_ms", 0))
                qwen_ms += latency
            except Exception:
                failures += 1
                fallback += 1
                output = rule_fallback(document)
                method = ExtractionMethod.RULE_FALLBACK
                latency = 0.0
                flags.append(QualityFlag.LOW_CONFIDENCE)
            result = TextAnalysisRecord(
                analysis_id="txa_" + key[:32],
                document_id=document.document_id,
                content_hash=document.content_hash,
                symbol=document.symbol,
                output=output,
                extraction_method=method,
                provider=self.client.provider,
                model_name=str(metadata.get("model_name", self.client.model_name)),
                prompt_version=TEXT_PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                algorithm_version=TEXT_ALGORITHM_VERSION,
                cache_key=key,
                processed_at=datetime.now(tz=SHANGHAI),
                latency_ms=latency,
                input_tokens=metadata.get("input_tokens"),
                output_tokens=metadata.get("output_tokens"),
                quality_flags=sorted(set(flags), key=str),
            )
            return self.cache.save_news_analysis(result)

        results = list(await asyncio.gather(*(one(item) for item in unique.values())))
        docs = {item.content_hash: unique[item.content_hash] for item in results}
        tick = perf_counter()
        events = self._events(results, docs)
        cluster_ms = (perf_counter() - tick) * 1000
        news = [
            NewsSentimentRecord(
                record_id="nsr_" + x.analysis_id[4:],
                analysis_id=x.analysis_id,
                document_id=x.document_id,
                content_hash=x.content_hash,
                symbol=x.symbol,
                text_type=x.output.text_type,
                direction=x.output.direction,
                confidence=x.output.confidence,
                horizon=x.output.horizon,
                novelty=x.output.novelty,
                source_quality=x.output.source_quality,
                available_time=x.output.available_time,
                evidence=x.output.evidence,
            )
            for x in results
            if x.output.text_type in NEWS_TYPES
        ]
        stats = LLMCallStats(
            documents_seen=seen,
            cache_hits=hits,
            cache_misses=misses,
            qwen_calls=calls,
            fallback_calls=fallback,
            qwen_failures=failures,
            duplicate_suppressed=seen - len(unique),
            qwen_latency_ms=qwen_ms,
            hash_ms=hash_ms,
            cache_lookup_ms=lookup_ms,
            clustering_ms=cluster_ms,
        )
        return results, events, news, stats

    @staticmethod
    def _events(analyses, documents):
        grouped = {}
        for analysis in analyses:
            if analysis.output.event_type != EventType.OTHER:
                doc = documents[analysis.content_hash]
                grouped.setdefault(canonical_event_id(doc, analysis.output), []).append(
                    (analysis, doc)
                )
        events = []
        for cid, group in sorted(grouped.items()):
            group.sort(
                key=lambda p: (
                    p[1].source_quality != TextSourceQuality.OFFICIAL_DISCLOSURE,
                    -p[0].output.confidence,
                )
            )
            primary, doc = group[0]
            hashes = sorted({x.content_hash for x, _ in group})
            official = any(
                d.source_quality
                in {TextSourceQuality.OFFICIAL_DISCLOSURE, TextSourceQuality.OFFICIAL_REGULATOR}
                for _, d in group
            )
            eid = (
                "evt_"
                + hashlib.sha256(
                    canonical_json({"cid": cid, "hashes": hashes}).encode()
                ).hexdigest()[:32]
            )
            events.append(
                EventIntelligenceRecord(
                    event_id=eid,
                    canonical_event_id=cid,
                    symbol=primary.symbol,
                    event_type=primary.output.event_type,
                    direction=primary.output.direction,
                    intensity=primary.output.intensity,
                    confidence=max(x.output.confidence for x, _ in group),
                    horizon=primary.output.horizon,
                    novelty=Novelty.NEW if len(group) == 1 else Novelty.UPDATE,
                    surprise=primary.output.surprise,
                    surprise_basis=primary.output.surprise_basis,
                    event_time=primary.output.event_time or doc.event_time,
                    published_time=doc.published_time,
                    available_time=min(d.available_time for _, d in group),
                    event_session=classify_event_session(
                        primary.output.event_time or doc.available_time
                    ),
                    source_quality=doc.source_quality,
                    fact_status=primary.output.fact_status,
                    evidence=primary.output.evidence,
                    source_document_hashes=hashes,
                    official_confirmation_exists=official,
                    risk_event_candidate=primary.output.text_type == TextType.RISK
                    or primary.output.event_type in RISK_EVENTS,
                    extraction_method=primary.extraction_method,
                    provider=primary.provider,
                    model_name=primary.model_name,
                    prompt_version=primary.prompt_version,
                    schema_version=primary.schema_version,
                    algorithm_version=EVENT_ALGORITHM_VERSION,
                    processed_at=max(x.processed_at for x, _ in group),
                )
            )
        return events


def calculate_news_snapshot(
    context: SnapshotBuildContext,
    records: Iterable[NewsSentimentRecord],
    *,
    window_start: datetime,
    window_end: datetime,
    raw_document_count: int | None = None,
) -> NewsSentimentSnapshot:
    values = [
        x
        for x in records
        if x.symbol == context.symbol_scope and window_start <= x.available_time <= window_end
    ]
    hashes = {x.content_hash for x in values}
    conf = sorted(x.confidence for x in values)
    directions = Counter(x.direction.value for x in values)
    types = Counter(x.text_type.value for x in values)
    qualities = Counter(x.source_quality.value for x in values)
    raw = len(values) if raw_document_count is None else raw_document_count
    payload = {
        "symbol": context.symbol_scope,
        "window_start": window_start,
        "window_end": window_end,
        "document_count": raw,
        "unique_document_count": len(hashes),
        "source_count": len(qualities),
        "opinion_count": types[TextType.OPINION.value],
        "expectation_change_count": types[TextType.EXPECTATION_CHANGE.value],
        "market_reaction_text_count": types[TextType.MARKET_REACTION.value],
        "rumor_count": types[TextType.RUMOR.value],
        "positive_count": directions[TextDirection.POSITIVE.value],
        "negative_count": directions[TextDirection.NEGATIVE.value],
        "neutral_count": directions[TextDirection.NEUTRAL.value],
        "mixed_count": directions[TextDirection.MIXED.value],
        "unknown_count": directions[TextDirection.UNKNOWN.value],
        "confidence_min": conf[0] if conf else None,
        "confidence_median": median(conf) if conf else None,
        "confidence_max": conf[-1] if conf else None,
        "novel_document_count": sum(x.novelty == Novelty.NEW for x in values),
        "duplicate_document_count": max(0, raw - len(hashes)),
        "source_quality_distribution": dict(sorted(qualities.items())),
        "latest_available_time": max((x.available_time for x in values), default=None),
        "field_provenance": {
            "document_count": "ATTENTION_PROXY",
            "direction_counts": "TEXT_EXTRACTION",
        },
    }
    meta = _meta(
        context=context,
        snapshot_type=SnapshotType.NEWS,
        algorithm_version=NEWS_SNAPSHOT_ALGORITHM_VERSION,
        normalized_payload=payload,
        coverage_ratio=1.0,
        quality_flags=set(context.quality_flags),
        missing_fields=set(),
    )
    return NewsSentimentSnapshot(meta=meta, **payload)


def calculate_event_snapshot(
    context: SnapshotBuildContext, event: EventIntelligenceRecord
) -> EventIntelligenceSnapshot:
    payload = {"event": event.model_dump(mode="json")}
    flags = set(context.quality_flags)
    missing: set[str] = set()
    if event.event_time is None:
        missing.add("event_time")
    meta = _meta(
        context=context,
        snapshot_type=SnapshotType.EVENT_INTELLIGENCE,
        algorithm_version=EVENT_ALGORITHM_VERSION,
        normalized_payload=payload,
        coverage_ratio=1.0,
        quality_flags=flags,
        missing_fields=missing,
    )
    return EventIntelligenceSnapshot(meta=meta, event=event)


__all__ = [
    "QwenExtractionClient",
    "TextIntelligenceProcessor",
    "analysis_cache_key",
    "build_text_document",
    "calculate_event_snapshot",
    "calculate_news_snapshot",
    "canonical_event_id",
    "classify_event_session",
    "document_content_hash",
    "rule_fallback",
]
