from __future__ import annotations

from router.schemas import (
    NewsPipelineRequest,
    NewsPipelineResponse,
    NewsProcessorOutput,
    RouterInvokeRequest,
)
from router.services.invocation import (
    RouterInvocationService,
)
from router.services.news_bundle import (
    NewsBundleService,
    build_news_processor_prompt,
)


class NewsAnalysisPipelineService:
    """Run Data Hub to news_processor analysis pipeline."""

    async def run(
        self,
        request: NewsPipelineRequest,
    ) -> NewsPipelineResponse:
        bundle = await NewsBundleService().build(
            symbol=request.symbol,
            start_date=request.start_date.isoformat(),
            end_date=request.end_date.isoformat(),
            finance_news_limit=(
                request.finance_news_limit
            ),
            announcement_limit=(
                request.announcement_limit
            ),
        )

        if not bundle.records:
            raise RuntimeError(
                "Data Hub 没有返回可处理的新闻或公告"
            )

        prompt = build_news_processor_prompt(bundle)

        invocation = await RouterInvocationService().invoke(
            RouterInvokeRequest(
                role="news_processor",
                symbol=request.symbol,
                prompt=prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        )

        if invocation.task_id is None:
            raise RuntimeError(
                "Router 调用没有生成 task_id"
            )

        output = NewsProcessorOutput.model_validate_json(
            invocation.content
        )

        return NewsPipelineResponse(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
            finance_news_count=(
                bundle.finance_news_count
            ),
            announcement_count=(
                bundle.announcement_count
            ),
            source_record_count=len(bundle.records),
            selected_record_ids=[
                record.record_id
                for record in bundle.records
            ],
            task_id=invocation.task_id,
            call_ids=invocation.call_ids,
            attempts=invocation.attempts,
            validated=invocation.validated,
            provider=invocation.provider,
            model=invocation.model,
            latency_ms=invocation.latency_ms,
            usage=invocation.usage,
            output=output,
        )