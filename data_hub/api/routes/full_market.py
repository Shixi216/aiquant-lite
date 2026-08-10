from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    CandidateEnrichmentRequest,
    CandidateEnrichmentResponse,
    DataCoverageResponse,
    DataExpansionRequest,
    DataExpansionResponse,
    ExpansionType,
    MarketSnapshotResponse,
    MarketSnapshotSyncRequest,
    StockUniverseRecord,
    UniverseListResponse,
    UniverseSyncRequest,
    UniverseSyncResponse,
)
from data_hub.services.candidate_enrichment_service import (
    CandidateEnrichmentService,
)
from data_hub.services.coverage_service import DataCoverageService
from data_hub.services.data_expansion_service import DataExpansionService
from data_hub.services.entity_linking_service import EntityLinkingService
from data_hub.services.market_snapshot_service import MarketSnapshotService
from data_hub.services.universe_service import StockUniverseService


router = APIRouter(tags=["full-market-data-foundation"])
repository = FullMarketRepository()
universe_service = StockUniverseService(repository=repository)
snapshot_service = MarketSnapshotService(repository=repository)
expansion_service = DataExpansionService(repository=repository)
entity_service = EntityLinkingService(repository=repository)
coverage_service = DataCoverageService(repository=repository)
enrichment_service = CandidateEnrichmentService(
    repository=repository,
    expansion_service=expansion_service,
)


@router.post(
    "/v1/universe/sync",
    response_model=UniverseSyncResponse,
    summary="Synchronize a versioned, auditable A-share universe",
)
def sync_universe(request: UniverseSyncRequest) -> UniverseSyncResponse:
    try:
        return universe_service.sync(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/v1/universe",
    response_model=UniverseListResponse,
    summary="Read a versioned A-share universe",
)
def list_universe(
    version: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    exchange: str | None = Query(default=None),
    board: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=5000),
) -> UniverseListResponse:
    actual_version, total, items = repository.list_universe(
        version=version,
        active_only=active_only,
        exchange=exchange,
        board=board,
        offset=offset,
        limit=limit,
    )
    return UniverseListResponse(
        universe_version=actual_version,
        total=total,
        offset=offset,
        limit=limit,
        items=items,
    )


@router.get(
    "/v1/universe/{symbol}",
    response_model=StockUniverseRecord,
    summary="Read one stock from a versioned universe",
)
def get_universe_stock(
    symbol: str,
    version: str | None = Query(default=None),
    data_cutoff: datetime | None = Query(default=None),
) -> StockUniverseRecord:
    item = repository.get_stock(
        symbol.upper(),
        version=version,
        data_cutoff=data_cutoff,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"stock not found: {symbol}")
    return item


@router.post(
    "/v1/market-snapshots/sync",
    response_model=MarketSnapshotResponse,
    summary="Fetch one batch full-market snapshot",
)
def sync_market_snapshot(
    request: MarketSnapshotSyncRequest,
) -> MarketSnapshotResponse:
    try:
        return snapshot_service.sync(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/v1/market-snapshots/latest",
    response_model=MarketSnapshotResponse,
    summary="Read the latest snapshot with explicit stale semantics",
)
def latest_market_snapshot(
    maximum_age_seconds: int = Query(default=300, ge=0, le=86_400),
    limit: int = Query(default=100, ge=1, le=5000),
) -> MarketSnapshotResponse:
    result = snapshot_service.latest(
        maximum_age_seconds=maximum_age_seconds,
        limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="no market snapshot")
    return result


@router.post(
    "/v1/data-expansion/run",
    response_model=DataExpansionResponse,
    summary="Run a dry-run-first budgeted data expansion",
)
def run_data_expansion(
    request: DataExpansionRequest,
) -> DataExpansionResponse:
    try:
        if request.expansion_type == ExpansionType.ENTITY_LINKS:
            return entity_service.build(request)
        return expansion_service.run(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/v1/data-coverage/latest",
    response_model=DataCoverageResponse,
    summary="Read the latest numerator-and-denominator coverage report",
)
def latest_data_coverage() -> DataCoverageResponse:
    result = repository.latest_coverage()
    if result is None:
        raise HTTPException(status_code=404, detail="no coverage report")
    return result


@router.post(
    "/v1/candidates/enrich",
    response_model=CandidateEnrichmentResponse,
    summary="Enrich at most 20 candidates in RESEARCH mode",
)
def enrich_candidates(
    request: CandidateEnrichmentRequest,
) -> CandidateEnrichmentResponse:
    try:
        return enrichment_service.enrich(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
