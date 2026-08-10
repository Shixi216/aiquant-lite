from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from config.settings import settings
from router.config import router_settings
from router.services.audit import AuditStore
from router.services.provider_registry import get_model_provider
from trading.research.policy_news.models import (
    EventBundle,
    PolicyExtractionResult,
    PolicyNewsModelProvider,
)
from trading.research.policy_news.policy import POLICY_NEWS_PROMPT_VERSION
from trading.research.policy_news.schemas import (
    ModelPolicyExtraction,
    PolicyEventCategory,
    PolicyEventType,
    PolicyFactType,
    PolicyImpactHorizon,
    PolicyRiskFlag,
    SchemaValidationStatus,
    TextCompleteness,
)


ProviderFactory = Callable[[str], PolicyNewsModelProvider | None]

_SYSTEM_PROMPT = """
Return exactly one JSON object matching the supplied schema.
Extract only facts explicitly present in the supplied evidence.
Never infer missing policy text, amounts, dates, conditions, sectors or stocks.
Do not output BUY, SELL, HOLD, position sizing, target prices, stop levels,
guaranteed returns, hidden reasoning, or executable instructions.
Implementation status is only a candidate; local code decides the final status
and all weights and scores.
""".strip()

_RULES: tuple[
    tuple[
        tuple[str, ...],
        PolicyEventCategory,
        PolicyEventType,
        int,
        float,
        PolicyImpactHorizon,
    ],
    ...,
] = (
    (
        ("行政处罚", "处罚决定", "市场禁入"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.ADMINISTRATIVE_PENALTY,
        -1,
        0.9,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("立案调查", "证监会立案", "被立案"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.REGULATORY_INVESTIGATION,
        -1,
        0.9,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("问询函", "监管问询"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.EXCHANGE_INQUIRY,
        -1,
        0.6,
        PolicyImpactHorizon.SHORT_TERM,
    ),
    (
        ("财务造假", "虚假记载"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.FRAUD_INVESTIGATION,
        -1,
        1.0,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("信息披露违规", "信披违规"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.DISCLOSURE_VIOLATION,
        -1,
        0.8,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("环保处罚", "安全生产处罚", "重大安全事故"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.SAFETY_ENVIRONMENTAL_PENALTY,
        -1,
        0.8,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("反垄断调查",),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.ANTITRUST_INVESTIGATION,
        -1,
        0.85,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("责令整改", "监管整改"),
        PolicyEventCategory.REGULATORY_ACTION,
        PolicyEventType.REGULATORY_REMEDIATION,
        -1,
        0.65,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("重大合同", "中标通知", "签订合同"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.MAJOR_CONTRACT,
        1,
        0.7,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("资产重组", "重大资产重组"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.ASSET_RESTRUCTURING,
        0,
        0.85,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("并购", "收购"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.MERGER_ACQUISITION,
        0,
        0.75,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("控制权变更", "实际控制人变更"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.CONTROL_CHANGE,
        0,
        0.85,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("股权转让",),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.EQUITY_TRANSFER,
        0,
        0.65,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("重大诉讼", "重大仲裁"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.MAJOR_LITIGATION,
        -1,
        0.75,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("重大担保", "对外担保"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.MAJOR_GUARANTEE,
        0,
        0.55,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("重大投资", "投资建设", "对外投资", "资产划转", "增资"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.MAJOR_INVESTMENT,
        0,
        0.65,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("投产", "产能投放"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.CAPACITY_LAUNCH,
        1,
        0.7,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("产品获批", "注册获批", "取得批准"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.PRODUCT_APPROVAL,
        1,
        0.75,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("项目终止", "终止项目"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.CORE_PROJECT_TERMINATION,
        -1,
        0.8,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("债务违约", "未能清偿到期债务"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.DEBT_DEFAULT,
        -1,
        1.0,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("破产重整", "预重整"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.BANKRUPTCY_REORGANIZATION,
        -1,
        0.9,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("停牌原因", "复牌原因", "重大事项停牌"),
        PolicyEventCategory.CORPORATE_MAJOR_EVENT,
        PolicyEventType.MATERIAL_SUSPENSION_REASON,
        0,
        0.6,
        PolicyImpactHorizon.SHORT_TERM,
    ),
    (
        ("财政支持", "专项资金", "财政补助"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.FISCAL_SUPPORT,
        1,
        0.7,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("税收优惠", "税费减免"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.TAX_INCENTIVE,
        1,
        0.7,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("补贴政策", "产业补贴"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.SUBSIDY,
        1,
        0.65,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("准入放宽", "放宽准入"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.MARKET_ACCESS,
        1,
        0.65,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("产业规划", "发展规划"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.INDUSTRIAL_PLAN,
        1,
        0.55,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("技术标准", "国家标准", "行业标准"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.TECHNICAL_STANDARD,
        0,
        0.55,
        PolicyImpactHorizon.LONG_TERM,
    ),
    (
        ("政府采购",),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.GOVERNMENT_PROCUREMENT,
        1,
        0.65,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("试点政策", "开展试点", "试点方案"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.PILOT_POLICY,
        1,
        0.55,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
    (
        ("产能限制", "限产", "产能扩张政策"),
        PolicyEventCategory.INDUSTRIAL_SUPPORT,
        PolicyEventType.CAPACITY_POLICY,
        0,
        0.7,
        PolicyImpactHorizon.MEDIUM_TERM,
    ),
)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_payload(content: str) -> Any:
    normalized = content.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        normalized = "\n".join(lines).strip()
    return json.loads(normalized)


def _provider_ready(provider: str) -> bool:
    return {
        "longcat": router_settings.longcat_ready,
        "qwen": router_settings.qwen_ready,
        "deepseek": router_settings.deepseek_ready,
        "mimo": router_settings.mimo_ready,
    }.get(provider, False)


def assess_text_completeness(bundle: EventBundle) -> TextCompleteness:
    payload = bundle.primary_source.payload
    full_text = payload.get("full_text") or payload.get("document_text")
    if isinstance(full_text, str) and full_text.strip():
        return TextCompleteness.FULL_TEXT
    content = payload.get("content") or payload.get("body") or payload.get("text")
    if isinstance(content, str) and content.strip():
        return TextCompleteness.PARTIAL_TEXT
    if any(payload.get(key) for key in ("image_url", "image_path", "scan_pages")):
        return TextCompleteness.IMAGE_ONLY
    title = str(payload.get("title") or bundle.canonical_title or "").strip()
    metadata_keys = {
        "announcement_date",
        "published_at",
        "publisher",
        "short_name",
        "symbol",
        "category",
        "url",
        "official_url",
    }
    if title and any(payload.get(key) for key in metadata_keys):
        return TextCompleteness.TITLE_AND_METADATA
    if title:
        return TextCompleteness.TITLE_ONLY
    return TextCompleteness.UNREADABLE


def _available_text(bundle: EventBundle) -> str:
    payload = bundle.primary_source.payload
    parts = [
        str(payload.get(key) or "").strip()
        for key in ("title", "full_text", "document_text", "content", "body", "text")
    ]
    return "\n".join(part for part in parts if part)


def _extract_explicit_values(
    text: str,
    *,
    completeness: TextCompleteness,
) -> tuple[list[str], list[str], list[str]]:
    if completeness not in {
        TextCompleteness.FULL_TEXT,
        TextCompleteness.PARTIAL_TEXT,
    }:
        return [], [], []
    amounts = list(
        dict.fromkeys(
            match.group(0)
            for match in re.finditer(
                r"\d+(?:\.\d+)?\s*(?:亿元|万元|元|%|吨|万股|股)",
                text,
            )
        )
    )[:20]
    dates = list(
        dict.fromkeys(
            match.group(0)
            for match in re.finditer(
                r"\d{4}年\d{1,2}月(?:\d{1,2}日)?",
                text,
            )
        )
    )[:20]
    conditions = [
        line.strip()[:300]
        for line in re.split(r"[。；\n]", text)
        if any(marker in line for marker in ("条件", "前提", "达到", "不低于"))
    ][:20]
    return amounts, dates, conditions


def normalize_event(
    bundle: EventBundle,
    *,
    completeness: TextCompleteness,
) -> ModelPolicyExtraction:
    text = _available_text(bundle)
    title = bundle.canonical_title.strip()
    for markers, category, event_type, direction, intensity, horizon in _RULES:
        if any(marker in text for marker in markers):
            amounts, dates, conditions = _extract_explicit_values(
                text,
                completeness=completeness,
            )
            confidence = 0.75
            if completeness == TextCompleteness.PARTIAL_TEXT:
                confidence = min(
                    confidence,
                    settings.policy_news_partial_text_confidence_cap,
                )
            elif completeness in {
                TextCompleteness.TITLE_AND_METADATA,
                TextCompleteness.TITLE_ONLY,
            }:
                confidence = min(
                    confidence,
                    settings.policy_news_title_only_confidence_cap,
                )
            entities = [
                str(value)
                for value in (
                    bundle.primary_source.payload.get("short_name"),
                    bundle.primary_source.payload.get("publisher"),
                )
                if value
            ]
            return ModelPolicyExtraction(
                event_category=category,
                event_type=event_type,
                direction=direction,
                intensity=intensity,
                confidence=confidence,
                fact_type=PolicyFactType.UNKNOWN,
                implementation_status_candidate="UNKNOWN",
                impact_horizon=horizon,
                affected_symbols=bundle.symbols,
                affected_sectors=bundle.sectors,
                summary=title[:1000],
                key_facts=[title[:500]],
                amounts=amounts,
                dates=dates,
                entities=list(dict.fromkeys(entities)),
                conditions=conditions,
            )
    return ModelPolicyExtraction(
        event_category=PolicyEventCategory.NOT_APPLICABLE,
        event_type=PolicyEventType.NOT_APPLICABLE,
        direction=0,
        intensity=0,
        confidence=0,
        fact_type=PolicyFactType.UNKNOWN,
        implementation_status_candidate="UNKNOWN",
        impact_horizon=PolicyImpactHorizon.UNKNOWN,
        affected_symbols=[],
        affected_sectors=[],
        summary=(
            f"事件不属于政策消息 v1 的三类明确范围：{title}"
        )[:1000],
        key_facts=[],
        amounts=[],
        dates=[],
        entities=[],
        conditions=[],
    )


def _primary_route(
    bundle: EventBundle,
    completeness: TextCompleteness,
) -> tuple[str, str]:
    if completeness == TextCompleteness.IMAGE_ONLY:
        return "mimo", "vision_reader"
    if bundle.cluster_event_type == "announcement":
        return "qwen", "announcement_verifier"
    return "longcat", "news_processor"


def _prompt(
    bundle: EventBundle,
    completeness: TextCompleteness,
) -> str:
    primary = bundle.primary_source
    safe_payload = {
        key: value
        for key, value in primary.payload.items()
        if key
        in {
            "title",
            "full_text",
            "document_text",
            "content",
            "publisher",
            "announcement_date",
            "published_at",
            "short_name",
            "symbol",
            "category",
        }
    }
    return json.dumps(
        {
            "schema": ModelPolicyExtraction.model_json_schema(),
            "text_completeness": completeness.value,
            "constraints": {
                "extract_only_explicit_facts": True,
                "title_only_amounts_must_be_empty": True,
                "no_trade_instructions": True,
            },
            "event": {
                "event_cluster_id": bundle.event_cluster_id,
                "canonical_title": bundle.canonical_title,
                "event_time": bundle.event_time.isoformat(),
                "source_level": primary.source_level,
                "source_count": bundle.source_count,
                "symbols": bundle.symbols,
                "sectors": bundle.sectors,
                "primary_payload": safe_payload,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


class PolicyNewsExtractor:
    """Structured candidates from models, with deterministic safe fallback."""

    def __init__(
        self,
        *,
        provider_factory: ProviderFactory = get_model_provider,
        audit_store: AuditStore | None = None,
    ) -> None:
        self.provider_factory = provider_factory
        self.audit_store = audit_store or AuditStore()

    def _audit(
        self,
        *,
        role: str,
        provider: str,
        response: Any | None,
        input_hash: str,
        retry_count: int,
        validation: SchemaValidationStatus,
        error: Exception | None = None,
    ) -> str:
        return self.audit_store.record_model_call(
            task_id=None,
            agent_role=role,
            provider=provider,
            model=(
                str(response.model)
                if response is not None
                else "unavailable"
            ),
            usage=response.usage if response is not None else {},
            latency_ms=(
                response.latency_ms if response is not None else None
            ),
            success=(
                error is None
                and validation
                in {
                    SchemaValidationStatus.VALID,
                    SchemaValidationStatus.REPAIRED,
                }
            ),
            error_type=type(error).__name__ if error is not None else None,
            error_message=str(error) if error is not None else None,
            prompt_version=POLICY_NEWS_PROMPT_VERSION,
            input_hash=input_hash,
            retry_count=retry_count,
            schema_validation=validation.value,
        )

    async def _invoke_validated(
        self,
        *,
        provider_name: str,
        role: str,
        prompt: str,
    ) -> tuple[
        ModelPolicyExtraction | None,
        list[str],
        SchemaValidationStatus,
    ]:
        provider = self.provider_factory(provider_name)
        if provider is None or not _provider_ready(provider_name):
            return None, [], SchemaValidationStatus.NOT_CALLED
        input_hash = _stable_hash(
            {
                "prompt_version": POLICY_NEWS_PROMPT_VERSION,
                "prompt": prompt,
                "role": role,
                "provider": provider_name,
            }
        )
        call_ids: list[str] = []
        current_prompt = prompt
        for attempt in range(1 + settings.policy_news_model_max_retries):
            response = None
            try:
                response = await provider.invoke(
                    role=role,
                    prompt=current_prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    temperature=0.0,
                    max_tokens=1200,
                )
                extraction = ModelPolicyExtraction.model_validate(
                    _json_payload(response.content)
                )
                status = (
                    SchemaValidationStatus.VALID
                    if attempt == 0
                    else SchemaValidationStatus.REPAIRED
                )
                call_ids.append(
                    self._audit(
                        role=role,
                        provider=provider_name,
                        response=response,
                        input_hash=input_hash,
                        retry_count=attempt,
                        validation=status,
                    )
                )
                return extraction, call_ids, status
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                call_ids.append(
                    self._audit(
                        role=role,
                        provider=provider_name,
                        response=response,
                        input_hash=input_hash,
                        retry_count=attempt,
                        validation=SchemaValidationStatus.INVALID,
                        error=exc,
                    )
                )
                if attempt >= settings.policy_news_model_max_retries:
                    break
                current_prompt = (
                    "Repair the output into exactly one valid JSON object. "
                    "Do not add facts or prose.\n" + prompt
                )
            except Exception as exc:
                call_ids.append(
                    self._audit(
                        role=role,
                        provider=provider_name,
                        response=response,
                        input_hash=input_hash,
                        retry_count=attempt,
                        validation=SchemaValidationStatus.INVALID,
                        error=exc,
                    )
                )
                break
        return None, call_ids, SchemaValidationStatus.INVALID

    @staticmethod
    def _needs_escalation(extraction: ModelPolicyExtraction) -> bool:
        return (
            extraction.event_type
            in {
                PolicyEventType.REGULATORY_INVESTIGATION,
                PolicyEventType.ADMINISTRATIVE_PENALTY,
                PolicyEventType.FRAUD_INVESTIGATION,
                PolicyEventType.ASSET_RESTRUCTURING,
                PolicyEventType.CONTROL_CHANGE,
                PolicyEventType.DEBT_DEFAULT,
                PolicyEventType.BANKRUPTCY_REORGANIZATION,
            }
            or extraction.confidence
            < settings.policy_news_escalation_confidence_threshold
            or extraction.intensity
            >= settings.policy_news_escalation_intensity_threshold
            or extraction.direction == 0
            and extraction.intensity >= 0.7
        )

    async def extract(
        self,
        bundle: EventBundle,
        *,
        allow_model_calls: bool,
    ) -> PolicyExtractionResult:
        completeness = assess_text_completeness(bundle)
        fallback = normalize_event(bundle, completeness=completeness)
        if not allow_model_calls:
            return PolicyExtractionResult(
                extraction=fallback,
                validation_status=SchemaValidationStatus.NOT_CALLED,
            )
        provider_name, role = _primary_route(bundle, completeness)
        if completeness == TextCompleteness.IMAGE_ONLY and not (
            _provider_ready("mimo") and _provider_ready("qwen")
        ):
            return PolicyExtractionResult(
                extraction=fallback,
                validation_status=SchemaValidationStatus.NOT_CALLED,
                risk_flags=[PolicyRiskFlag.MODEL_CALL_FAILED],
                provider="mimo",
            )
        extraction, call_ids, status = await self._invoke_validated(
            provider_name=provider_name,
            role=role,
            prompt=_prompt(bundle, completeness),
        )
        if extraction is None:
            return PolicyExtractionResult(
                extraction=fallback,
                model_call_ids=call_ids,
                validation_status=status,
                risk_flags=[
                    (
                        PolicyRiskFlag.MODEL_CALL_FAILED
                        if status == SchemaValidationStatus.NOT_CALLED
                        else PolicyRiskFlag.MODEL_OUTPUT_INVALID
                    )
                ],
                provider=provider_name,
            )
        flags: list[PolicyRiskFlag] = []
        if completeness in {
            TextCompleteness.TITLE_ONLY,
            TextCompleteness.TITLE_AND_METADATA,
        }:
            extraction = extraction.model_copy(
                update={
                    "amounts": [],
                    "conditions": [],
                    "confidence": min(
                        extraction.confidence,
                        settings.policy_news_title_only_confidence_cap,
                    ),
                }
            )
        if completeness == TextCompleteness.IMAGE_ONLY:
            review, review_calls, review_status = await self._invoke_validated(
                provider_name="qwen",
                role="announcement_verifier",
                prompt=_prompt(bundle, completeness),
            )
            call_ids.extend(review_calls)
            if review is None:
                return PolicyExtractionResult(
                    extraction=fallback,
                    model_call_ids=call_ids,
                    validation_status=review_status,
                    risk_flags=[PolicyRiskFlag.MODEL_OUTPUT_INVALID],
                    provider="mimo",
                )
            extraction = review.model_copy(
                update={"fact_type": PolicyFactType.MODEL_INFERENCE}
            )
        if self._needs_escalation(extraction) and _provider_ready("deepseek"):
            review, review_calls, review_status = await self._invoke_validated(
                provider_name="deepseek",
                role="risk_controller",
                prompt=_prompt(bundle, completeness),
            )
            call_ids.extend(review_calls)
            if review is not None and (
                review.direction != extraction.direction
                or review.event_type != extraction.event_type
            ):
                extraction = extraction.model_copy(
                    update={
                        "direction": 0,
                        "confidence": min(
                            extraction.confidence,
                            review.confidence,
                        ),
                    }
                )
                flags.append(PolicyRiskFlag.EVENT_CONFLICT)
            elif review_status == SchemaValidationStatus.INVALID:
                flags.append(PolicyRiskFlag.MODEL_OUTPUT_INVALID)
        return PolicyExtractionResult(
            extraction=extraction,
            model_call_ids=list(dict.fromkeys(call_ids)),
            validation_status=status,
            risk_flags=flags,
            used_model=True,
            provider=provider_name,
        )


__all__ = [
    "PolicyNewsExtractor",
    "assess_text_completeness",
    "normalize_event",
]
