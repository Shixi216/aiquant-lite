from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException

from router.schemas import (
    AnnouncementVerificationPipelineRequest,
    AnnouncementVerificationPipelineResponse,
)
from router.services.announcement_pipeline import (
    AnnouncementVerificationPipelineService,
)


router = APIRouter(
    prefix="/v1/pipelines",
    tags=["pipelines"],
)


@router.post(
    "/announcement-verification",
    response_model=(
        AnnouncementVerificationPipelineResponse
    ),
    summary="运行官方公告核验流水线",
)
async def run_announcement_verification(
    request: AnnouncementVerificationPipelineRequest,
) -> AnnouncementVerificationPipelineResponse:
    try:
        return await (
            AnnouncementVerificationPipelineService()
            .run(request)
        )

    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "公告上游接口返回错误："
                f"HTTP {exc.response.status_code}"
            ),
        ) from exc

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "公告上游网络连接失败："
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

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "公告核验流水线发生未处理异常："
                f"{type(exc).__name__}"
            ),
        ) from exc