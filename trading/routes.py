from __future__ import annotations

from datetime import date as Date

from fastapi import APIRouter, Header, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import Field

from trading.decision_support.decision_packets import (
    DecisionImmutableError,
    DecisionIntegrityError,
    DecisionNotFoundError,
    DecisionRepository,
    DecisionVersionError,
    SourceTraceabilityError,
)
from trading.decision_support.orchestrator import DecisionService
from manual_tracking.trades import (
    ManualPreviewExpiredError,
    ManualPreviewIntegrityError,
    ManualPreviewNotFoundError,
    ManualPreviewStateError,
    ManualTradeConflictError,
    ManualTradeIdentityError,
    ManualTradeNotFoundError,
    ManualTradeRepository,
    ManualTradeRepositoryError,
)
from manual_tracking.trades import ManualTradeService
from manual_tracking.risk_reviews import (
    ManualPositionRiskEvidenceError,
    ManualPositionRiskRepositoryError,
    ManualPositionRiskReviewNotFoundError,
    ManualPositionRiskReviewRepository,
)
from manual_tracking.risk_reviews import (
    ManualPositionRiskReviewService,
    RouterHighRiskReviewer,
)
from trading.simulation.persistence import TradingAuditStore
from trading.research.events.position_data import PositionResearchDataService
from trading.review.attribution import (
    DecisionReadRepository,
    ManualPositionRiskReviewReadRepository,
    ManualTradeReadRepository,
    SimulationReadRepository,
)
from trading.review.daily import ReviewService
from trading.schemas import (
    BacktestRequest,
    BacktestResult,
    DailyReview,
    DecisionChallengeRequest,
    DecisionChallengeResult,
    DecisionFromDataRequest,
    DecisionPacket,
    DecisionRequest,
    ManualPosition,
    ManualPositionRiskReview,
    ManualPositionRiskReviewRequest,
    ManualTrade,
    ManualTradeConfirmation,
    ManualTradeCorrectionRequest,
    ManualTradePreview,
    ManualTradePreviewCreateRequest,
    OptimizationRequest,
    OptimizationResult,
    OrderIntent,
    OrderResult,
    PaperAccount,
    RiskLimits,
    TradingModel,
)
from trading.simulation.service import SimulationService


DEPRECATION_VALUE = "true"
SUNSET_VALUE = "Thu, 31 Dec 2026 23:59:59 GMT"
DEPRECATED_RESPONSE_DOCS = {
    200: {
        "headers": {
            "Deprecation": {
                "description": "This legacy route is deprecated.",
                "schema": {"type": "string", "example": DEPRECATION_VALUE},
            },
            "Sunset": {
                "description": "Planned removal date for the legacy route.",
                "schema": {"type": "string", "example": SUNSET_VALUE},
            },
        }
    }
}

router = APIRouter()
decision_router = APIRouter(prefix="/v1/decisions", tags=["decision-support"])
simulation_router = APIRouter(prefix="/v1/simulations", tags=["simulations"])
review_router = APIRouter(prefix="/v1/reviews", tags=["reviews"])
manual_trade_router = APIRouter(prefix="/v1/manual-trades", tags=["manual-trades"])
manual_position_router = APIRouter(
    prefix="/v1/manual-positions",
    tags=["manual-positions"],
)
legacy_router = APIRouter(prefix="/v1/trading", tags=["deprecated-trading-aliases"])

audit_store = TradingAuditStore()
decision_repository = DecisionRepository()
decision_service = DecisionService(decision_repository)
simulation_service = SimulationService(audit_store=audit_store)
manual_trade_repository = ManualTradeRepository()
manual_trade_service = ManualTradeService(
    manual_trade_repository,
    decision_repository,
)
manual_position_risk_repository = ManualPositionRiskReviewRepository()
decision_read_repository = DecisionReadRepository(decision_repository)
simulation_read_repository = SimulationReadRepository(audit_store)
manual_trade_read_repository = ManualTradeReadRepository(manual_trade_repository)
manual_position_risk_read_repository = ManualPositionRiskReviewReadRepository(
    manual_position_risk_repository
)
review_service = ReviewService(
    decisions=decision_read_repository,
    simulations=simulation_read_repository,
    manual_trades=manual_trade_read_repository,
    risk_reviews=manual_position_risk_read_repository,
)
manual_position_risk_service = ManualPositionRiskReviewService(
    manual_trades=manual_trade_read_repository,
    decisions=decision_read_repository,
    reviews=manual_position_risk_repository,
    research=PositionResearchDataService(),
    high_risk_reviewer=RouterHighRiskReviewer(),
)

# Kept as an internal import compatibility alias for local tests and operators.
paper_trading = simulation_service.paper_trading


class PaperOrderRequest(TradingModel):
    intent: OrderIntent
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)


class ProtectiveExitRequest(TradingModel):
    prices: dict[str, float]
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)


class KillSwitchRequest(TradingModel):
    active: bool


class DailyReviewRequest(TradingModel):
    date: Date


def _mark_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = DEPRECATION_VALUE
    response.headers["Sunset"] = SUNSET_VALUE


def _decision_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DecisionNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (DecisionIntegrityError, DecisionImmutableError, DecisionVersionError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _manual_trade_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ManualPreviewNotFoundError, ManualTradeNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ManualTradeIdentityError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ManualPreviewExpiredError):
        return HTTPException(status_code=410, detail=str(exc))
    if isinstance(
        exc,
        (
            ManualPreviewIntegrityError,
            ManualPreviewStateError,
            ManualTradeConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _manual_position_risk_http_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (ManualTradeNotFoundError, ManualPositionRiskReviewNotFoundError),
    ):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ManualPositionRiskEvidenceError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ManualPositionRiskRepositoryError):
        return HTTPException(status_code=422, detail=str(exc))
    return _manual_trade_http_error(exc)


async def _decision_from_data(request: DecisionFromDataRequest) -> DecisionPacket:
    try:
        return await decision_service.create_decision_from_data(request)
    except (
        DecisionIntegrityError,
        DecisionImmutableError,
        DecisionVersionError,
        SourceTraceabilityError,
        ValueError,
    ) as exc:
        raise _decision_http_error(exc) from exc


@decision_router.post("/from-data", response_model=DecisionPacket)
async def create_decision_from_data(request: DecisionFromDataRequest) -> DecisionPacket:
    return await _decision_from_data(request)


@decision_router.get("/{decision_id}", response_model=DecisionPacket)
def get_decision(decision_id: str) -> DecisionPacket:
    try:
        return decision_service.get_latest(decision_id)
    except (DecisionNotFoundError, DecisionIntegrityError) as exc:
        raise _decision_http_error(exc) from exc


@decision_router.get(
    "/{decision_id}/versions",
    response_model=list[DecisionPacket],
)
def list_decision_versions(decision_id: str) -> list[DecisionPacket]:
    try:
        return decision_service.list_versions(decision_id)
    except (DecisionNotFoundError, DecisionIntegrityError) as exc:
        raise _decision_http_error(exc) from exc


@decision_router.get(
    "/{decision_id}/versions/{version}",
    response_model=DecisionPacket,
)
def get_decision_version(decision_id: str, version: int) -> DecisionPacket:
    try:
        return decision_service.get_version(decision_id, version)
    except (DecisionNotFoundError, DecisionIntegrityError) as exc:
        raise _decision_http_error(exc) from exc


@decision_router.post(
    "/{decision_id}/challenge",
    response_model=DecisionChallengeResult,
)
def challenge_decision(
    decision_id: str,
    request: DecisionChallengeRequest,
) -> DecisionChallengeResult:
    try:
        return decision_service.challenge(decision_id, request)
    except (
        DecisionNotFoundError,
        DecisionIntegrityError,
        DecisionImmutableError,
        DecisionVersionError,
        SourceTraceabilityError,
        ValueError,
    ) as exc:
        raise _decision_http_error(exc) from exc


@simulation_router.post("/backtests", response_model=BacktestResult)
def create_backtest(request: BacktestRequest) -> BacktestResult:
    return simulation_service.backtest(request)


@simulation_router.post(
    "/portfolio/optimize",
    response_model=OptimizationResult,
)
def create_portfolio_optimization(request: OptimizationRequest) -> OptimizationResult:
    return simulation_service.optimize_portfolio(request)


@simulation_router.post("/paper-orders", response_model=OrderResult)
def create_paper_order(request: PaperOrderRequest) -> OrderResult:
    return simulation_service.submit_paper_order(request.intent, request.risk_limits)


@simulation_router.get("/paper-account", response_model=PaperAccount)
def get_paper_account() -> PaperAccount:
    return simulation_service.paper_account()


@simulation_router.post(
    "/protective-exits",
    response_model=list[OrderResult],
)
def create_protective_exits(request: ProtectiveExitRequest) -> list[OrderResult]:
    return simulation_service.execute_protective_exits(
        request.prices,
        request.risk_limits,
    )


@simulation_router.post("/kill-switch", response_model=PaperAccount)
def update_kill_switch(request: KillSwitchRequest) -> PaperAccount:
    return simulation_service.set_kill_switch(request.active)


@review_router.post("/daily", response_model=DailyReview)
def create_daily_review(request: DailyReviewRequest) -> DailyReview:
    return review_service.daily_review(request.date)


@review_router.get("/daily/{date}", response_model=DailyReview)
def get_daily_review(date: Date) -> DailyReview:
    return review_service.daily_review(date)


@review_router.get("/daily/{date}/markdown", response_class=PlainTextResponse)
def get_daily_review_markdown(date: Date) -> str:
    return review_service.daily_review_markdown(date)


@manual_trade_router.post("/previews", response_model=ManualTradePreview)
def create_manual_trade_preview(
    request: ManualTradePreviewCreateRequest,
    authenticated_user: str = Header(alias="X-Authenticated-User"),
    authenticated_channel: str = Header(alias="X-Authenticated-Channel"),
) -> ManualTradePreview:
    try:
        return manual_trade_service.create_preview(
            request.trade,
            requested_by=authenticated_user,
            channel=authenticated_channel,
            expires_in_seconds=request.expires_in_seconds,
        )
    except (ManualTradeRepositoryError, DecisionNotFoundError, ValueError) as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_trade_router.get(
    "/previews/{confirmation_id}",
    response_model=ManualTradePreview,
)
def get_manual_trade_preview(confirmation_id: str) -> ManualTradePreview:
    try:
        return manual_trade_service.get_preview(confirmation_id)
    except ManualTradeRepositoryError as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_trade_router.post(
    "/previews/{confirmation_id}/confirm",
    response_model=ManualTradeConfirmation,
)
def confirm_manual_trade_preview(
    confirmation_id: str,
    authenticated_user: str = Header(alias="X-Authenticated-User"),
    authenticated_channel: str = Header(alias="X-Authenticated-Channel"),
) -> ManualTradeConfirmation:
    try:
        return manual_trade_service.confirm_preview(
            confirmation_id,
            confirmed_by=authenticated_user,
            channel=authenticated_channel,
        )
    except ManualTradeRepositoryError as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_trade_router.post(
    "/previews/{confirmation_id}/cancel",
    response_model=ManualTradePreview,
)
def cancel_manual_trade_preview(
    confirmation_id: str,
    authenticated_user: str = Header(alias="X-Authenticated-User"),
    authenticated_channel: str = Header(alias="X-Authenticated-Channel"),
) -> ManualTradePreview:
    try:
        return manual_trade_service.cancel_preview(
            confirmation_id,
            cancelled_by=authenticated_user,
            channel=authenticated_channel,
        )
    except ManualTradeRepositoryError as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_trade_router.get("", response_model=list[ManualTrade])
def list_manual_trades(
    portfolio_id: str | None = None,
    symbol: str | None = None,
) -> list[ManualTrade]:
    return manual_trade_service.list_trades(
        portfolio_id=portfolio_id,
        symbol=symbol,
    )


@manual_trade_router.get("/{trade_id}", response_model=ManualTrade)
def get_manual_trade(trade_id: str) -> ManualTrade:
    try:
        return manual_trade_service.get_trade(trade_id)
    except ManualTradeRepositoryError as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_trade_router.post(
    "/{trade_id}/corrections",
    response_model=ManualTradePreview,
)
def create_manual_trade_correction_preview(
    trade_id: str,
    request: ManualTradeCorrectionRequest,
    authenticated_user: str = Header(alias="X-Authenticated-User"),
    authenticated_channel: str = Header(alias="X-Authenticated-Channel"),
) -> ManualTradePreview:
    try:
        return manual_trade_service.create_correction_preview(
            trade_id,
            request,
            requested_by=authenticated_user,
            channel=authenticated_channel,
        )
    except (ManualTradeRepositoryError, ValueError) as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_position_router.get("", response_model=list[ManualPosition])
def list_manual_positions(
    portfolio_id: str | None = None,
    symbol: str | None = None,
) -> list[ManualPosition]:
    return manual_trade_service.list_positions(
        portfolio_id=portfolio_id,
        symbol=symbol,
    )


@manual_position_router.get("/{position_id}", response_model=ManualPosition)
def get_manual_position(position_id: str) -> ManualPosition:
    try:
        return manual_trade_service.get_position(position_id)
    except ManualTradeRepositoryError as exc:
        raise _manual_trade_http_error(exc) from exc


@manual_position_router.post(
    "/{position_id}/risk-reviews",
    response_model=ManualPositionRiskReview,
)
async def create_manual_position_risk_review(
    position_id: str,
    request: ManualPositionRiskReviewRequest,
    authenticated_user: str = Header(alias="X-Authenticated-User"),
    authenticated_channel: str = Header(alias="X-Authenticated-Channel"),
) -> ManualPositionRiskReview:
    try:
        return await manual_position_risk_service.review_position(
            position_id,
            request,
            created_by=authenticated_user,
            channel=authenticated_channel,
        )
    except (ManualTradeRepositoryError, ManualPositionRiskRepositoryError) as exc:
        raise _manual_position_risk_http_error(exc) from exc


@manual_position_router.get(
    "/{position_id}/risk-reviews",
    response_model=list[ManualPositionRiskReview],
)
def list_manual_position_risk_reviews(
    position_id: str,
) -> list[ManualPositionRiskReview]:
    try:
        return manual_position_risk_service.list_reviews(position_id)
    except (ManualTradeRepositoryError, ManualPositionRiskRepositoryError) as exc:
        raise _manual_position_risk_http_error(exc) from exc


@manual_position_router.get(
    "/{position_id}/risk-reviews/latest",
    response_model=ManualPositionRiskReview,
)
def get_latest_manual_position_risk_review(
    position_id: str,
) -> ManualPositionRiskReview:
    try:
        return manual_position_risk_service.latest_review(position_id)
    except (ManualTradeRepositoryError, ManualPositionRiskRepositoryError) as exc:
        raise _manual_position_risk_http_error(exc) from exc


@legacy_router.post(
    "/decisions",
    response_model=DecisionPacket,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
async def deprecated_create_decision(
    request: DecisionRequest,
    response: Response,
) -> DecisionPacket:
    _mark_deprecated(response)
    try:
        return await decision_service.create_decision(request)
    except (
        DecisionIntegrityError,
        DecisionImmutableError,
        DecisionVersionError,
        SourceTraceabilityError,
        ValueError,
    ) as exc:
        raise _decision_http_error(exc) from exc


@legacy_router.post(
    "/decisions/from-data",
    response_model=DecisionPacket,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
async def deprecated_create_decision_from_data(
    request: DecisionFromDataRequest,
    response: Response,
) -> DecisionPacket:
    _mark_deprecated(response)
    return await _decision_from_data(request)


@legacy_router.post(
    "/backtests",
    response_model=BacktestResult,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_backtest(
    request: BacktestRequest,
    response: Response,
) -> BacktestResult:
    _mark_deprecated(response)
    return simulation_service.backtest(request)


@legacy_router.post(
    "/portfolio/optimize",
    response_model=OptimizationResult,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_portfolio_optimization(
    request: OptimizationRequest,
    response: Response,
) -> OptimizationResult:
    _mark_deprecated(response)
    return simulation_service.optimize_portfolio(request)


@legacy_router.post(
    "/paper/orders",
    response_model=OrderResult,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_submit_paper_order(
    request: PaperOrderRequest,
    response: Response,
) -> OrderResult:
    _mark_deprecated(response)
    return simulation_service.submit_paper_order(request.intent, request.risk_limits)


@legacy_router.get(
    "/paper/account",
    response_model=PaperAccount,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_paper_account(response: Response) -> PaperAccount:
    _mark_deprecated(response)
    return simulation_service.paper_account()


@legacy_router.post(
    "/paper/protective-exits",
    response_model=list[OrderResult],
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_execute_protective_exits(
    request: ProtectiveExitRequest,
    response: Response,
) -> list[OrderResult]:
    _mark_deprecated(response)
    return simulation_service.execute_protective_exits(
        request.prices,
        request.risk_limits,
    )


@legacy_router.post(
    "/paper/kill-switch",
    response_model=PaperAccount,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_set_paper_kill_switch(
    request: KillSwitchRequest,
    response: Response,
) -> PaperAccount:
    _mark_deprecated(response)
    return simulation_service.set_kill_switch(request.active)


@legacy_router.get(
    "/reviews/daily/{review_date}",
    response_model=DailyReview,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_daily_review(
    review_date: Date,
    response: Response,
) -> DailyReview:
    _mark_deprecated(response)
    return review_service.daily_review(review_date)


@legacy_router.get(
    "/reviews/daily/{review_date}/markdown",
    response_class=PlainTextResponse,
    deprecated=True,
    responses=DEPRECATED_RESPONSE_DOCS,
)
def deprecated_daily_review_markdown(
    review_date: Date,
    response: Response,
) -> str:
    _mark_deprecated(response)
    return review_service.daily_review_markdown(review_date)


router.include_router(decision_router)
router.include_router(simulation_router)
router.include_router(review_router)
router.include_router(manual_trade_router)
router.include_router(manual_position_router)
router.include_router(legacy_router)
