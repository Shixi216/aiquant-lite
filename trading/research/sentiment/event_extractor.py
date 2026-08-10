from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from config.settings import settings
from router.config import router_settings
from router.services.audit import AuditStore
from router.services.provider_registry import get_model_provider
from trading.research.sentiment.event_normalizer import normalize_event
from trading.research.sentiment.models import (
    EventBundle,
    ExtractionResult,
    SentimentModelProvider,
)
from trading.research.sentiment.policy import SENTIMENT_PROMPT_VERSION
from trading.research.sentiment.schemas import (
    ModelSentimentExtraction,
    SchemaValidationStatus,
    SentimentEventType,
    SentimentRiskFlag,
)


ProviderFactory = Callable[[str], SentimentModelProvider | None]

_SYSTEM_PROMPT = """
Return one strict JSON object matching the supplied schema.
Extract only concise structured event conclusions from the supplied evidence.
Do not output BUY, SELL, HOLD, position sizing, stop levels, target prices,
guaranteed returns, hidden reasoning, or any executable instruction.
Model output is an unverified inference; local deterministic code scores it.
""".strip()


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


def _primary_route(bundle: EventBundle) -> tuple[str, str]:
    # 新闻处理用 qwen（唯一稳定可用的事件提取模型）
    # 公告提取用 longcat 试验（对比 qwen 在公告场景的效果）
    if bundle.cluster_event_type == "announcement":
        return "longcat", "announcement_verifier"
    return "qwen", "news_processor"


def _enhanced_schema() -> dict:
    """生成带枚举可选值的 schema，确保模型知道可选值范围。

    model_json_schema() 对 StrEnum 字段不输出 enum 列表（pydantic 2.13 的
    已知行为），导致模型自由发挥产生非法枚举值。这里手动补齐。
    """
    from trading.research.sentiment.schemas import (
        FactType, ImpactHorizon, ModelSentimentExtraction,
        SentimentEventType,
    )
    schema = ModelSentimentExtraction.model_json_schema()
    enum_values = {
        "event_type": [e.value for e in SentimentEventType],
        "impact_horizon": [e.value for e in ImpactHorizon],
        "fact_type": [e.value for e in FactType],
    }
    props = schema.setdefault("properties", {})
    for field, values in enum_values.items():
        if field in props:
            props[field]["enum"] = values
            props[field]["description"] = (
                props[field].get("description", "")
                + f" 可选值(必须从中选择): {', '.join(values)}"
            )
    return schema


def _prompt(bundle: EventBundle) -> str:
    primary = bundle.primary_source
    safe_payload = {
        key: value
        for key, value in primary.payload.items()
        if key
        in {
            "title",
            "content",
            "publisher",
            "announcement_date",
            "short_name",
            "symbol",
            "category",
        }
    }
    return json.dumps(
        {
            "schema": _enhanced_schema(),
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


class SentimentEventExtractor:
    """Optional model extraction with one repair and deterministic fallback."""

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
            usage=(
                response.usage
                if response is not None
                else {}
            ),
            latency_ms=(
                response.latency_ms
                if response is not None
                else None
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
            prompt_version=SENTIMENT_PROMPT_VERSION,
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
        ModelSentimentExtraction | None,
        list[str],
        SchemaValidationStatus,
    ]:
        provider = self.provider_factory(provider_name)
        if provider is None or not _provider_ready(provider_name):
            return None, [], SchemaValidationStatus.NOT_CALLED
        # 备用模型链：主模型失败时切换（qwen 稳定 → longcat 兜底）
        fallback_chain = {
            "qwen": ["longcat"],
            "longcat": ["qwen"],
        }.get(provider_name, [])
        input_hash = _stable_hash(
            {
                "prompt_version": SENTIMENT_PROMPT_VERSION,
                "prompt": prompt,
                "role": role,
                "provider": provider_name,
            }
        )
        call_ids: list[str] = []
        current_prompt = prompt
        max_attempts = 1 + settings.sentiment_model_max_retries
        # 尝试队列：先主模型，失败后切备用模型
        providers_to_try = [provider_name, *fallback_chain]
        tried = 0
        for provider_name_attempt in providers_to_try:
            if not _provider_ready(provider_name_attempt):
                continue
            provider = self.provider_factory(provider_name_attempt)
            for attempt in range(max_attempts):
                response = None
                try:
                    response = await provider.invoke(
                        role=role,
                        prompt=current_prompt,
                        system_prompt=_SYSTEM_PROMPT,
                        temperature=0.0,
                        max_tokens=800,
                    )
                    extraction = ModelSentimentExtraction.model_validate(
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
                            provider=provider_name_attempt,
                            response=response,
                            input_hash=input_hash,
                            retry_count=tried,
                            validation=status,
                        )
                    )
                    return extraction, call_ids, status
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    call_ids.append(
                        self._audit(
                            role=role,
                            provider=provider_name_attempt,
                            response=response,
                            input_hash=input_hash,
                            retry_count=tried,
                            validation=SchemaValidationStatus.INVALID,
                            error=exc,
                        )
                    )
                    tried += 1
                    if attempt + 1 >= max_attempts:
                        break
                    current_prompt = (
                        "Repair the previous output into exactly one valid JSON "
                        "object. Do not add prose. Original evidence and schema:\n"
                        + prompt
                    )
                except Exception as exc:
                    call_ids.append(
                        self._audit(
                            role=role,
                            provider=provider_name_attempt,
                            response=response,
                            input_hash=input_hash,
                            retry_count=tried,
                            validation=SchemaValidationStatus.INVALID,
                            error=exc,
                        )
                    )
                    tried += 1
                    break
        return None, call_ids, SchemaValidationStatus.INVALID

    @staticmethod
    def _needs_escalation(
        extraction: ModelSentimentExtraction,
    ) -> bool:
        return (
            extraction.event_type
            in {
                SentimentEventType.REGULATORY_PENALTY,
                SentimentEventType.INVESTIGATION,
                SentimentEventType.MAJOR_LITIGATION,
            }
            or extraction.confidence
            < settings.sentiment_escalation_confidence_threshold
            or extraction.intensity
            >= settings.sentiment_escalation_intensity_threshold
        )

    async def extract(
        self,
        bundle: EventBundle,
        *,
        allow_model_calls: bool,
    ) -> ExtractionResult:
        fallback = normalize_event(bundle)
        if not allow_model_calls:
            return ExtractionResult(
                extraction=fallback,
                validation_status=SchemaValidationStatus.NOT_CALLED,
                used_model=False,
            )

        provider_name, role = _primary_route(bundle)
        extraction, call_ids, status = await self._invoke_validated(
            provider_name=provider_name,
            role=role,
            prompt=_prompt(bundle),
        )
        flags: list[SentimentRiskFlag] = []
        if extraction is None:
            flags.append(
                SentimentRiskFlag.MODEL_CALL_FAILED
                if status == SchemaValidationStatus.NOT_CALLED
                else SentimentRiskFlag.MODEL_OUTPUT_INVALID
            )
            return ExtractionResult(
                extraction=fallback,
                model_call_ids=call_ids,
                validation_status=status,
                risk_flags=flags,
                used_model=False,
                provider=provider_name,
            )

        if self._needs_escalation(extraction) and _provider_ready("deepseek"):
            review, review_calls, review_status = await self._invoke_validated(
                provider_name="deepseek",
                role="risk_controller",
                prompt=_prompt(bundle),
            )
            call_ids.extend(review_calls)
            if review is not None and review.direction != extraction.direction:
                extraction = extraction.model_copy(
                    update={
                        "direction": 0,
                        "confidence": min(
                            extraction.confidence,
                            review.confidence,
                        ),
                        "summary": extraction.summary,
                    }
                )
                flags.append(SentimentRiskFlag.EVENT_CONFLICT)
            elif review is not None:
                extraction = extraction.model_copy(
                    update={
                        "intensity": max(
                            extraction.intensity,
                            review.intensity,
                        ),
                        "confidence": min(
                            extraction.confidence,
                            review.confidence,
                        ),
                    }
                )
            elif review_status == SchemaValidationStatus.INVALID:
                flags.append(SentimentRiskFlag.MODEL_OUTPUT_INVALID)

        return ExtractionResult(
            extraction=extraction,
            model_call_ids=call_ids,
            validation_status=status,
            risk_flags=flags,
            used_model=True,
            provider=provider_name,
        )


__all__ = ["SentimentEventExtractor"]
