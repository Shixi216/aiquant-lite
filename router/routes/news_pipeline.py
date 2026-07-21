from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from router.schemas import (
    NewsPipelineRequest,
    NewsPipelineResponse,
)
from router.services.news_pipeline import (
    NewsAnalysisPipelineService,
)


router = APIRouter(
    prefix="/v1/pipelines",
    tags=["pipelines"],
)


@router.post(
    "/news-analysis",
    response_model=NewsPipelineResponse,
    summary="运行新闻与公告分析流水线",
)
async def run_news_analysis(
    request: NewsPipelineRequest,
) -> NewsPipelineResponse:
    try:
        return await NewsAnalysisPipelineService().run(
            request
        )

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Data Hub 上游接口返回错误："
                f"HTTP {exc.response.status_code}"
            ),
        ) from exc

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Data Hub 网络连接失败："
                f"{type(exc).__name__}"
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc