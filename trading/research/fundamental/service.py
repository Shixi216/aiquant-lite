from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Callable

from config.settings import settings
from data_hub.repositories import FactorOutputRepository
from data_hub.services.financial_statement_service import (
    FinancialStatementService,
)
from trading.research.factor_adapters import fundamental_factor_output
from trading.research.fundamental.metrics import (
    CORE_METRIC_FIELDS,
    FUNDAMENTAL_METRICS_VERSION,
    calculate_fundamental_metrics,
    report_period_as_of,
)
from trading.research.fundamental.models import (
    FinancialFetcher,
    FundamentalAnalysisRequest,
    FundamentalAnalysisResult,
    FundamentalMetrics,
    FundamentalRiskFlag,
    PointInTimeFinancialRecord,
    ScreeningFundamentalSummary,
)
from trading.research.fundamental.policy import policy_for, stale_days
from trading.research.fundamental.repository import FundamentalRepository
from trading.schemas import (
    AnalysisMode,
    FundamentalDataStatus,
    FundamentalSnapshot,
)


FUNDAMENTAL_ANALYSIS_VERSION = "fundamental-point-in-time-v1"


def _stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique_flags(
    flags: list[FundamentalRiskFlag],
) -> list[FundamentalRiskFlag]:
    return sorted(set(flags), key=lambda flag: flag.value)


def _current_status(
    *,
    missing_fields: list[str],
    conflict_only: bool,
    has_records: bool,
) -> FundamentalDataStatus:
    if conflict_only:
        return FundamentalDataStatus.CONFLICT
    if not has_records or len(missing_fields) == len(CORE_METRIC_FIELDS):
        return FundamentalDataStatus.UNAVAILABLE
    if missing_fields:
        return FundamentalDataStatus.PARTIAL
    return FundamentalDataStatus.AVAILABLE


class FundamentalAnalysisService:
    """One point-in-time engine with explicit, centralized mode policies."""

    def __init__(
        self,
        repository: FundamentalRepository | None = None,
        factor_repository: FactorOutputRepository | None = None,
        fetcher_factory: Callable[[], FinancialFetcher] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or FundamentalRepository()
        self.factor_repository = (
            factor_repository or FactorOutputRepository()
        )
        self.fetcher_factory = (
            fetcher_factory or FinancialStatementService
        )
        self.now_factory = (
            now_factory or (lambda: datetime.now().astimezone())
        )

    @staticmethod
    def _selectable_records(
        records: list[PointInTimeFinancialRecord],
    ) -> list[PointInTimeFinancialRecord]:
        return [
            record
            for record in records
            if record.verification_status.value != "CONFLICT"
        ]

    def _fetch_if_allowed(
        self,
        *,
        request: FundamentalAnalysisRequest,
        has_records: bool,
        now: datetime,
    ) -> tuple[bool, str, bool]:
        policy = policy_for(request.analysis_mode)
        if has_records:
            return False, "NOT_NEEDED", False
        if not policy.auto_fetch:
            return False, "SKIPPED_MODE_RESTRICTION", False
        if not request.auto_fetch:
            return False, "SKIPPED_DISABLED", False
        historical_limit = now - timedelta(
            days=settings.fundamental_current_fetch_max_age_days
        )
        if request.data_cutoff < historical_limit:
            return False, "SKIPPED_HISTORICAL_DATA_GAP", True

        start = request.data_cutoff - timedelta(
            days=settings.fundamental_fetch_lookback_days
        )
        last_error: Exception | None = None
        for attempt in range(
            1,
            settings.fundamental_fetch_max_attempts + 1,
        ):
            try:
                self.fetcher_factory().get_financial_statement(
                    request.symbol,
                    start.strftime("%Y%m%d"),
                    request.data_cutoff.strftime("%Y%m%d"),
                    persist=True,
                )
                return True, f"SUCCESS_ATTEMPT_{attempt}", False
            except Exception as exc:
                last_error = exc
        error_name = (
            type(last_error).__name__
            if last_error is not None
            else "UnknownError"
        )
        return True, f"FAILED:{error_name}", False

    @staticmethod
    def _confidence(
        records: list[PointInTimeFinancialRecord],
        missing_fields: list[str],
        *,
        manual_override: bool,
    ) -> float:
        if manual_override:
            base = settings.fundamental_confidence_disclosure_missing
        elif not records:
            return 0.0
        elif any(
            record.verification_status.value == "CONFLICT"
            for record in records
        ):
            return settings.fundamental_confidence_conflict
        elif all(
            record.verification_status.value == "VERIFIED"
            for record in records
        ):
            base = settings.fundamental_confidence_verified
        else:
            base = settings.fundamental_confidence_single_source
        available_ratio = (
            len(CORE_METRIC_FIELDS) - len(missing_fields)
        ) / len(CORE_METRIC_FIELDS)
        return max(0.0, min(1.0, base * available_ratio))

    @staticmethod
    def _manual_missing_fields(
        snapshot: FundamentalSnapshot,
    ) -> list[str]:
        return [
            field
            for field in CORE_METRIC_FIELDS
            if getattr(snapshot, field) is None
        ]

    @staticmethod
    def _manual_metrics(
        snapshot: FundamentalSnapshot,
    ) -> FundamentalMetrics:
        return FundamentalMetrics(
            pe_ttm=snapshot.pe_ttm,
            pb=snapshot.pb,
            roe=snapshot.roe,
            revenue_growth=snapshot.revenue_growth,
            net_profit_growth=snapshot.net_profit_growth,
            debt_ratio=snapshot.debt_ratio,
            operating_cash_flow=snapshot.operating_cash_flow,
            operating_cash_flow_positive=(
                snapshot.operating_cash_flow_positive
            ),
            peg=snapshot.peg,
            report_period=snapshot.report_period,
            statement_types=snapshot.statement_types,
        )

    def analyze(
        self,
        request: FundamentalAnalysisRequest,
    ) -> FundamentalAnalysisResult:
        now = self.now_factory()
        if request.data_cutoff.tzinfo is None:
            raise ValueError("data_cutoff must include a timezone")
        if request.data_cutoff > now:
            raise ValueError("data_cutoff must not be in the future")
        policy = policy_for(request.analysis_mode)

        records = self.repository.list_at_cutoff(
            symbol=request.symbol,
            data_cutoff=request.data_cutoff,
            allow_missing_disclosure=(
                policy.allow_missing_disclosure_reference
            ),
            include_conflicts=policy.include_conflicts_as_reference,
        )
        attempted, fetch_result, historical_gap = self._fetch_if_allowed(
            request=request,
            has_records=bool(records),
            now=now,
        )
        if attempted and fetch_result.startswith("SUCCESS"):
            records = self.repository.list_at_cutoff(
                symbol=request.symbol,
                data_cutoff=request.data_cutoff,
                allow_missing_disclosure=(
                    policy.allow_missing_disclosure_reference
                ),
                include_conflicts=policy.include_conflicts_as_reference,
            )

        conflicts = self.repository.conflicts(
            symbol=request.symbol,
            data_cutoff=request.data_cutoff,
        )
        usable_records = self._selectable_records(records)
        valuation_point = self.repository.latest_market_point(
            symbol=request.symbol,
            data_cutoff=request.data_cutoff,
        )
        (
            metrics,
            missing_fields,
            metric_flags,
            selected_records,
        ) = calculate_fundamental_metrics(
            records=usable_records,
            valuation_point=valuation_point,
        )
        risk_flags = list(metric_flags)
        if conflicts:
            risk_flags.append(FundamentalRiskFlag.FINANCIAL_CONFLICT)
        if historical_gap and not usable_records:
            risk_flags.append(
                FundamentalRiskFlag.HISTORICAL_DATA_GAP
            )
        if not usable_records:
            risk_flags.append(FundamentalRiskFlag.DATA_GAP)
        if any(
            record.data_available_time is None
            for record in selected_records
        ):
            risk_flags.extend(
                [
                    FundamentalRiskFlag.DISCLOSURE_TIME_MISSING,
                    FundamentalRiskFlag.UNVERIFIED_DATA,
                ]
            )
        if any(
            record.verification_status.value == "SINGLE_SOURCE"
            for record in selected_records
        ):
            risk_flags.append(
                FundamentalRiskFlag.SINGLE_SOURCE_DATA
            )
        if metrics.report_period:
            report_time = report_period_as_of(
                metrics.report_period,
                request.data_cutoff,
            )
            if (
                request.data_cutoff - report_time
            ).days > stale_days(metrics.period_type):
                risk_flags.append(
                    FundamentalRiskFlag.STALE_FINANCIAL_DATA
                )

        manual_input_id: str | None = None
        manual_accepted = False
        if request.manual_snapshot is not None:
            manual_accepted = {
                AnalysisMode.SCREENING: True,
                AnalysisMode.RESEARCH: not usable_records,
                AnalysisMode.DECISION: (
                    request.allow_manual_override
                    and bool(request.manual_override_reason)
                    and request.manual_operator_confirmed
                ),
            }[request.analysis_mode]
            manual_input_id = self.repository.record_manual_input(
                symbol=request.symbol,
                snapshot=request.manual_snapshot,
                analysis_mode=request.analysis_mode,
                override_requested=request.allow_manual_override,
                override_reason=request.manual_override_reason,
                operator_confirmed=request.manual_operator_confirmed,
                accepted_for_analysis=manual_accepted,
                provided_at=now,
            )
            risk_flags.extend(
                [
                    FundamentalRiskFlag.USER_PROVIDED_DATA,
                    FundamentalRiskFlag.UNVERIFIED_DATA,
                ]
            )
            if not manual_accepted:
                risk_flags.append(
                    FundamentalRiskFlag.MODE_RESTRICTION
                )
            else:
                metrics = self._manual_metrics(
                    request.manual_snapshot
                )
                missing_fields = self._manual_missing_fields(
                    request.manual_snapshot
                )

        canonical_ids = [
            record.canonical_record_id
            for record in selected_records
        ]
        raw_ids = list(
            dict.fromkeys(
                source_id
                for record in selected_records
                for source_id in record.source_record_ids
            )
        )
        valuation_ids: list[str] = []
        if valuation_point is not None:
            valuation_ids = [
                valuation_point.canonical_record_id,
                *valuation_point.source_record_ids,
            ]
        announcement_times = [
            record.announcement_time
            for record in selected_records
            if record.announcement_time is not None
        ]
        availability_times = [
            record.data_available_time
            for record in selected_records
            if record.data_available_time is not None
        ]
        verification_status = None
        if selected_records:
            statuses = {
                record.verification_status.value
                for record in selected_records
            }
            verification_status = (
                next(iter(statuses))
                if len(statuses) == 1
                else "MIXED"
            )
        if manual_accepted:
            verification_status = "UNVERIFIED"

        as_of = report_period_as_of(
            metrics.report_period,
            request.data_cutoff,
        ).date()
        snapshot = FundamentalSnapshot(
            as_of=as_of,
            pe_ttm=metrics.pe_ttm,
            pb=metrics.pb,
            roe=metrics.roe,
            revenue_growth=metrics.revenue_growth,
            net_profit_growth=metrics.net_profit_growth,
            debt_ratio=metrics.debt_ratio,
            operating_cash_flow=metrics.operating_cash_flow,
            operating_cash_flow_positive=(
                metrics.operating_cash_flow_positive
            ),
            peg=metrics.peg,
            report_period=metrics.report_period,
            statement_types=metrics.statement_types,
            announcement_time=(
                max(announcement_times)
                if announcement_times
                else None
            ),
            data_available_time=(
                max(availability_times)
                if availability_times
                else None
            ),
            primary_source=(
                selected_records[0].primary_source
                if selected_records
                else "USER_PROVIDED"
                if manual_accepted
                else None
            ),
            verification_status=verification_status,
            source_type=(
                "USER_PROVIDED"
                if manual_accepted
                else "CANONICAL"
                if selected_records
                else None
            ),
            evidence_refs=[
                *(
                    f"data_record:{record_id}"
                    for record_id in raw_ids
                ),
                *(
                    f"canonical_financial:{record_id}"
                    for record_id in canonical_ids
                ),
            ],
        )
        conflict_only = bool(conflicts) and not usable_records
        status = _current_status(
            missing_fields=missing_fields,
            conflict_only=conflict_only,
            has_records=bool(usable_records) or manual_accepted,
        )
        risk_flags = _unique_flags(risk_flags)
        input_hash = _stable_hash(
            {
                "symbol": request.symbol,
                "mode": request.analysis_mode.value,
                "data_cutoff": request.data_cutoff.isoformat(),
                "metrics": metrics.model_dump(mode="json"),
                "canonical_record_ids": canonical_ids,
                "source_record_ids": raw_ids,
                "valuation_evidence_ids": valuation_ids,
                "manual_input_id": manual_input_id,
                "risk_flags": [flag.value for flag in risk_flags],
                "algorithm_version": FUNDAMENTAL_ANALYSIS_VERSION,
            }
        )
        result = FundamentalAnalysisResult(
            audit_id="fau_" + input_hash[:32],
            symbol=request.symbol,
            analysis_mode=request.analysis_mode,
            status=status,
            data_cutoff=request.data_cutoff,
            snapshot=snapshot,
            metrics=metrics,
            used_report_period=metrics.report_period,
            announcement_time=snapshot.announcement_time,
            data_available_time=snapshot.data_available_time,
            missing_fields=missing_fields,
            risk_flags=risk_flags,
            verification_status=verification_status,
            canonical_record_ids=canonical_ids,
            source_record_ids=raw_ids,
            valuation_evidence_ids=valuation_ids,
            manual_input_id=manual_input_id,
            auto_fetch_attempted=attempted,
            auto_fetch_result=fetch_result,
            algorithm_version=FUNDAMENTAL_ANALYSIS_VERSION,
            input_snapshot_hash=input_hash,
        )

        if request.analysis_mode == AnalysisMode.SCREENING:
            return result

        self.repository.record_analysis(result)
        evidence_ids = list(
            dict.fromkeys(
                [
                    result.audit_id,
                    *canonical_ids,
                    *raw_ids,
                    *valuation_ids,
                    *([manual_input_id] if manual_input_id else []),
                ]
            )
        )
        confidence = self._confidence(
            selected_records,
            missing_fields,
            manual_override=manual_accepted,
        )
        factor = fundamental_factor_output(
            symbol=request.symbol,
            snapshot=snapshot,
            evidence_ids=evidence_ids,
            data_cutoff=request.data_cutoff,
            generated_at=now,
            repository=(
                self.factor_repository
                if policy.persist_factor
                else None
            ),
            shadow_mode=policy.shadow_mode,
            confidence_override=confidence,
            risk_flags=[flag.value for flag in risk_flags],
            metadata={
                "analysis_mode": request.analysis_mode.value,
                "fundamental_audit_id": result.audit_id,
                "metrics": metrics.model_dump(mode="json"),
                "missing_fields": missing_fields,
                "verification_status": verification_status,
                "manual_input_id": manual_input_id,
                "formula_version": FUNDAMENTAL_METRICS_VERSION,
                "no_hidden_chain_of_thought": True,
            },
        )
        return result.model_copy(update={"factor_output": factor})


class ScreeningFundamentalService:
    def __init__(self, service: FundamentalAnalysisService) -> None:
        self.service = service

    def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        manual_snapshot: FundamentalSnapshot | None = None,
    ) -> FundamentalAnalysisResult:
        return self.service.analyze(
            FundamentalAnalysisRequest(
                symbol=symbol,
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=data_cutoff,
                auto_fetch=False,
                manual_snapshot=manual_snapshot,
            )
        )

    def analyze_many(
        self,
        *,
        symbols: list[str],
        data_cutoff: datetime,
    ) -> list[ScreeningFundamentalSummary]:
        unique_symbols = list(dict.fromkeys(symbols))
        records = self.service.repository.list_screening_records(
            symbols=unique_symbols,
            data_cutoff=data_cutoff,
        )
        grouped: dict[str, list[PointInTimeFinancialRecord]] = {
            symbol: [] for symbol in unique_symbols
        }
        for record in records:
            grouped.setdefault(record.symbol, []).append(record)
        summaries: list[ScreeningFundamentalSummary] = []
        for symbol in unique_symbols:
            symbol_records = grouped[symbol]
            conflicts = [
                record
                for record in symbol_records
                if record.verification_status.value == "CONFLICT"
            ]
            usable = [
                record
                for record in symbol_records
                if record.verification_status.value != "CONFLICT"
            ]
            metrics, missing, flags, selected = (
                calculate_fundamental_metrics(
                    records=usable,
                    valuation_point=None,
                )
            )
            if conflicts:
                flags.append(
                    FundamentalRiskFlag.FINANCIAL_CONFLICT
                )
            if any(
                record.data_available_time is None
                for record in selected
            ):
                flags.append(
                    FundamentalRiskFlag.DISCLOSURE_TIME_MISSING
                )
            if any(
                record.verification_status.value == "SINGLE_SOURCE"
                for record in selected
            ):
                flags.append(
                    FundamentalRiskFlag.SINGLE_SOURCE_DATA
                )
            status = _current_status(
                missing_fields=missing,
                conflict_only=bool(conflicts) and not usable,
                has_records=bool(usable),
            )
            statuses = {
                record.verification_status.value
                for record in selected
            }
            summaries.append(
                ScreeningFundamentalSummary(
                    symbol=symbol,
                    status=status,
                    used_report_period=metrics.report_period,
                    confidence=self.service._confidence(
                        selected,
                        missing,
                        manual_override=False,
                    ),
                    missing_fields=missing,
                    risk_flags=_unique_flags(flags),
                    verification_status=(
                        next(iter(statuses))
                        if len(statuses) == 1
                        else "MIXED"
                        if statuses
                        else None
                    ),
                )
            )
        return summaries


class ResearchFundamentalService:
    def __init__(self, service: FundamentalAnalysisService) -> None:
        self.service = service

    def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        auto_fetch: bool = True,
        manual_snapshot: FundamentalSnapshot | None = None,
    ) -> FundamentalAnalysisResult:
        return self.service.analyze(
            FundamentalAnalysisRequest(
                symbol=symbol,
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=data_cutoff,
                auto_fetch=auto_fetch,
                manual_snapshot=manual_snapshot,
            )
        )


class DecisionFundamentalService:
    def __init__(self, service: FundamentalAnalysisService) -> None:
        self.service = service

    def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        auto_fetch: bool = True,
        manual_snapshot: FundamentalSnapshot | None = None,
        allow_manual_override: bool = False,
        manual_override_reason: str | None = None,
        manual_operator_confirmed: bool = False,
    ) -> FundamentalAnalysisResult:
        return self.service.analyze(
            FundamentalAnalysisRequest(
                symbol=symbol,
                analysis_mode=AnalysisMode.DECISION,
                data_cutoff=data_cutoff,
                auto_fetch=auto_fetch,
                manual_snapshot=manual_snapshot,
                allow_manual_override=allow_manual_override,
                manual_override_reason=manual_override_reason,
                manual_operator_confirmed=manual_operator_confirmed,
            )
        )


__all__ = [
    "DecisionFundamentalService",
    "FUNDAMENTAL_ANALYSIS_VERSION",
    "FundamentalAnalysisService",
    "ResearchFundamentalService",
    "ScreeningFundamentalService",
]
