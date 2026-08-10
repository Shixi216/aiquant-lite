from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import math
from time import perf_counter
import tracemalloc
from typing import Any

from config.settings import settings
from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.capital_flow.aggregator import aggregate_symbol, stable_hash
from trading.research.capital_flow.market_features import aggregate_market
from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.policy import (
    CAPITAL_FLOW_ALGORITHM_VERSION,
    policy_for,
)
from trading.research.capital_flow.repository import CapitalFlowRepository
from trading.research.capital_flow.schemas import (
    CapitalFlowAnalyzeRequest,
    CapitalFlowAnalyzeResponse,
    CapitalFlowCandidate,
    CapitalFlowMarketSnapshot,
    CapitalFlowRiskFlag,
    CapitalFlowScope,
    CapitalFlowSectorSnapshot,
    CapitalFlowSymbolSnapshot,
)
from trading.research.capital_flow.sector_features import aggregate_sector
from trading.schemas import AnalysisMode


def _response(
    result: CapitalFlowSymbolSnapshot | CapitalFlowSectorSnapshot | CapitalFlowMarketSnapshot,
    *,
    candidates: list[CapitalFlowCandidate] | None = None,
) -> CapitalFlowAnalyzeResponse:
    sub_scores = (
        result.sub_scores
        if isinstance(result, CapitalFlowSymbolSnapshot)
        else {}
    )
    return CapitalFlowAnalyzeResponse(
        analysis_mode=result.analysis_mode,
        data_cutoff=result.data_cutoff,
        shadow_mode=True,
        score=result.score,
        confidence=result.confidence,
        sub_scores=sub_scores,
        missing_fields=result.missing_fields,
        risk_flags=result.risk_flags,
        evidence_ids=result.evidence_ids,
        sample_universe_size=result.sample_universe_size,
        partial_universe=result.partial_universe,
        result=result,
        candidates=candidates or [],
    )


@dataclass(frozen=True)
class ScreeningPerformance:
    symbol_count: int
    elapsed_seconds: float
    peak_memory_bytes: int
    database_connection_count: int = 0
    network_request_count: int = 0
    model_call_count: int = 0


class CapitalFlowAnalysisService:
    """Local deterministic capital-flow engine; it has no model client."""

    def __init__(
        self,
        repository: CapitalFlowRepository | None = None,
        factor_repository: FactorOutputRepository | None = None,
    ) -> None:
        self.repository = repository or CapitalFlowRepository()
        self.factor_repository = factor_repository or FactorOutputRepository()

    @staticmethod
    def _latest_amounts(
        bars_by_symbol: dict[str, list[MarketBar]],
    ) -> dict[str, float]:
        output: dict[str, float] = {}
        for symbol, bars in bars_by_symbol.items():
            if not bars:
                continue
            amount = max(bars, key=lambda item: item.event_time).amount
            if amount is not None and amount > 0:
                output[symbol] = amount
        return output

    def calculate_batch(
        self,
        *,
        bars_by_symbol: dict[str, list[MarketBar]],
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        symbols: list[str] | None = None,
    ) -> tuple[list[CapitalFlowSymbolSnapshot], CapitalFlowMarketSnapshot]:
        market = aggregate_market(
            bars_by_symbol=bars_by_symbol,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
        )
        market_amounts = self._latest_amounts(bars_by_symbol)
        ranked = sorted(
            market_amounts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        rank_map = {
            item[0]: index
            for index, item in enumerate(ranked, 1)
        }
        percentile_map = {
            item[0]: 1 - (index - 1) / len(ranked)
            for index, item in enumerate(ranked, 1)
        }
        selected = symbols or sorted(bars_by_symbol)
        snapshots: list[CapitalFlowSymbolSnapshot] = []
        for symbol in selected:
            bars = bars_by_symbol.get(symbol, [])
            if not bars:
                continue
            snapshots.append(
                aggregate_symbol(
                    symbol=symbol,
                    bars=bars,
                    analysis_mode=analysis_mode,
                    data_cutoff=data_cutoff,
                    market_amounts=market_amounts,
                    universe_complete=not market.partial_universe,
                    amount_market_ranks=rank_map,
                    amount_market_percentiles=percentile_map,
                    financing_records=[],
                )
            )
        snapshots.sort(key=lambda item: (-item.score, item.symbol))
        return snapshots, market

    @staticmethod
    def screen_candidates(
        *,
        bars_by_symbol: dict[str, list[MarketBar]],
        data_cutoff: datetime,
        symbols: list[str] | None = None,
    ) -> list[CapitalFlowCandidate]:
        selected = symbols or sorted(bars_by_symbol)
        candidates: list[CapitalFlowCandidate] = []
        for symbol in selected:
            ordered = sorted(
                (
                    bar
                    for bar in bars_by_symbol.get(symbol, [])
                    if bar.event_time <= data_cutoff
                    and bar.data_cutoff <= data_cutoff
                ),
                key=lambda item: item.event_time,
            )
            if not ordered:
                continue
            current = ordered[-1]
            history = ordered[:-1]
            prior_volumes = [
                bar.volume
                for bar in history[-5:]
                if bar.volume is not None and bar.volume > 0
            ]
            prior_amounts = [
                bar.amount
                for bar in history[-5:]
                if bar.amount is not None and bar.amount > 0
            ]
            volume_ratio = (
                current.volume / (sum(prior_volumes) / 5)
                if current.volume is not None
                and current.volume > 0
                and len(prior_volumes) == 5
                else None
            )
            amount_ratio = (
                current.amount / (sum(prior_amounts) / 5)
                if current.amount is not None
                and current.amount > 0
                and len(prior_amounts) == 5
                else None
            )
            components = [
                max(-1.0, min(1.0, math.log(value, 2) / 2))
                for value in (volume_ratio, amount_ratio)
                if value is not None and value > 0
            ]
            risks = {CapitalFlowRiskFlag.PARTIAL_UNIVERSE}
            missing = [
                "turnover_rate",
                "financing_balance",
                "sector_flow",
            ]
            if volume_ratio is None:
                missing.append("volume_ratio_5d")
            if amount_ratio is None:
                missing.append("amount_ratio_5d")
            if volume_ratio is None or amount_ratio is None:
                risks.add(CapitalFlowRiskFlag.INSUFFICIENT_HISTORY)
            if current.volume is not None and current.volume <= 0:
                risks.add(CapitalFlowRiskFlag.SUSPENDED_OR_ILLIQUID)
            score = sum(components) / len(components) if components else 0.0
            confidence = min(
                settings.capital_flow_partial_confidence_cap,
                0.1 + len(components) * 0.1,
            )
            identity = stable_hash(
                {
                    "symbol": symbol,
                    "cutoff": data_cutoff.isoformat(),
                    "evidence_id": current.canonical_record_id,
                    "mode": AnalysisMode.SCREENING.value,
                }
            )
            candidates.append(
                CapitalFlowCandidate(
                    symbol=symbol,
                    score=score,
                    confidence=confidence,
                    snapshot_id="cfc_" + identity[:32],
                    missing_fields=sorted(missing),
                    risk_flags=sorted(risks),
                    evidence_ids=[current.canonical_record_id],
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.symbol))
        return candidates

    def profile_screening(
        self,
        *,
        bars_by_symbol: dict[str, list[MarketBar]],
        data_cutoff: datetime,
    ) -> tuple[list[CapitalFlowCandidate], ScreeningPerformance]:
        tracemalloc.start()
        started = perf_counter()
        candidates = self.screen_candidates(
            bars_by_symbol=bars_by_symbol,
            data_cutoff=data_cutoff,
        )
        elapsed = perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return candidates, ScreeningPerformance(
            symbol_count=len(bars_by_symbol),
            elapsed_seconds=elapsed,
            peak_memory_bytes=peak,
        )

    def _persist_symbol_and_factor(
        self,
        snapshot: CapitalFlowSymbolSnapshot,
        *,
        persist_snapshot: bool,
        persist_factor: bool,
    ) -> tuple[CapitalFlowSymbolSnapshot, FactorOutput | None]:
        persisted = (
            self.repository.save_symbol_snapshot(snapshot)
            if persist_snapshot
            else snapshot
        )
        factor = (
            self.factor_from_snapshot(persisted, persist=True)
            if persist_factor
            else None
        )
        return persisted, factor

    async def analyze(
        self,
        request: CapitalFlowAnalyzeRequest,
    ) -> CapitalFlowAnalyzeResponse:
        now = datetime.now().astimezone()
        if request.data_cutoff > now:
            raise ValueError("data_cutoff must not be in the future")
        mode_policy = policy_for(request.analysis_mode)
        if request.analysis_mode == AnalysisMode.SCREENING and request.symbol:
            existing = self.repository.latest_symbol_snapshot(
                symbol=request.symbol,
                data_cutoff=request.data_cutoff,
            )
            if existing is not None:
                candidate = CapitalFlowCandidate(
                    symbol=existing.symbol,
                    score=existing.score,
                    confidence=existing.confidence,
                    snapshot_id=existing.snapshot_id,
                    missing_fields=existing.missing_fields,
                    risk_flags=existing.risk_flags,
                    evidence_ids=existing.evidence_ids,
                )
                return CapitalFlowAnalyzeResponse(
                    analysis_mode=AnalysisMode.SCREENING,
                    data_cutoff=request.data_cutoff,
                    shadow_mode=True,
                    score=candidate.score,
                    confidence=candidate.confidence,
                    sub_scores={},
                    missing_fields=candidate.missing_fields,
                    risk_flags=candidate.risk_flags,
                    evidence_ids=candidate.evidence_ids,
                    sample_universe_size=existing.sample_universe_size,
                    partial_universe=existing.partial_universe,
                    result=None,
                    candidates=[candidate],
                )
        bars_by_symbol = self.repository.market_bars(
            data_cutoff=request.data_cutoff,
            symbols=None,
        )
        selected_symbols = (
            list(dict.fromkeys([request.symbol, *request.symbols]))
            if request.symbol
            else request.symbols or None
        )
        if (
            request.analysis_mode == AnalysisMode.SCREENING
            and request.scope == CapitalFlowScope.SYMBOL
        ):
            candidates = self.screen_candidates(
                bars_by_symbol=bars_by_symbol,
                data_cutoff=request.data_cutoff,
                symbols=selected_symbols,
            )
            if not candidates:
                missing_symbol = request.symbol or ",".join(request.symbols)
                raise KeyError(
                    f"No local point-in-time capital data for {missing_symbol}"
                )
            top = candidates[0]
            return CapitalFlowAnalyzeResponse(
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=request.data_cutoff,
                shadow_mode=True,
                score=top.score,
                confidence=top.confidence,
                sub_scores={},
                missing_fields=top.missing_fields,
                risk_flags=top.risk_flags,
                evidence_ids=top.evidence_ids,
                sample_universe_size=len(bars_by_symbol),
                partial_universe=True,
                result=None,
                candidates=candidates,
            )
        snapshots, market = self.calculate_batch(
            bars_by_symbol=bars_by_symbol,
            analysis_mode=request.analysis_mode,
            data_cutoff=request.data_cutoff,
            symbols=selected_symbols,
        )
        persist_snapshot = mode_policy.persist_snapshot and request.persist is not False
        persist_factor = mode_policy.persist_factor and request.persist is not False
        if request.scope == CapitalFlowScope.MARKET:
            result = (
                self.repository.save_market_snapshot(market)
                if persist_snapshot
                else market
            )
            return _response(result)
        if request.scope == CapitalFlowScope.SECTOR:
            sector = request.sector or ""
            result = aggregate_sector(
                sector=sector,
                bars_by_symbol=bars_by_symbol,
                market_total_amount=market.market_total_amount,
                expected_symbols={
                    symbol
                    for symbol, bars in bars_by_symbol.items()
                    if bars and bars[-1].sector == sector
                },
                analysis_mode=request.analysis_mode,
                data_cutoff=request.data_cutoff,
            )
            if result is None:
                raise KeyError(f"No trusted sector mapping for {sector}")
            if persist_snapshot:
                result = self.repository.save_sector_snapshot(result)
            return _response(result)
        if not snapshots:
            missing_symbol = request.symbol or ",".join(request.symbols)
            raise KeyError(f"No local point-in-time capital data for {missing_symbol}")
        persisted: list[CapitalFlowSymbolSnapshot] = []
        for snapshot in snapshots:
            item, _ = self._persist_symbol_and_factor(
                snapshot,
                persist_snapshot=persist_snapshot,
                persist_factor=persist_factor,
            )
            persisted.append(item)
        candidates = [
            CapitalFlowCandidate(
                symbol=item.symbol,
                score=item.score,
                confidence=item.confidence,
                snapshot_id=item.snapshot_id,
                missing_fields=item.missing_fields,
                risk_flags=item.risk_flags,
            )
            for item in persisted
        ]
        return _response(persisted[0], candidates=candidates)

    def factor_from_snapshot(
        self,
        snapshot: CapitalFlowSymbolSnapshot,
        *,
        persist: bool,
    ) -> FactorOutput:
        metadata: dict[str, Any] = {
            "volume_score": snapshot.volume_score.value,
            "amount_score": snapshot.amount_score.value,
            "turnover_score": snapshot.turnover_score.value,
            "price_volume_score": snapshot.price_volume_score.value,
            "financing_score": snapshot.financing_score.value,
            "sector_flow_score": snapshot.sector_flow_score.value,
            "liquidity_score": snapshot.liquidity_score.value,
            "volume_ratio_20d": snapshot.volume_ratio_20d,
            "amount_ratio_20d": snapshot.amount_ratio_20d,
            "turnover_rate": snapshot.turnover_rate,
            "price_volume_state": snapshot.price_volume_state.value,
            "financing_trend": snapshot.financing_trend.value,
            "amount_market_percentile": snapshot.amount_market_percentile,
            "missing_fields": snapshot.missing_fields,
            "analysis_mode": snapshot.analysis_mode.value,
            "formal_strategy_weight": 0,
        }
        identity = {
            "snapshot_id": snapshot.snapshot_id,
            "factor_type": FactorType.CAPITAL_FLOW.value,
            "algorithm": CAPITAL_FLOW_ALGORITHM_VERSION,
        }
        factor_hash = stable_hash(identity)
        factor = FactorOutput(
            factor_id="fac_" + factor_hash[:32],
            symbol=snapshot.symbol,
            factor_type=FactorType.CAPITAL_FLOW,
            score=snapshot.score,
            confidence=snapshot.confidence,
            data_cutoff=snapshot.data_cutoff,
            generated_at=snapshot.generated_at,
            evidence_ids=list(
                dict.fromkeys([snapshot.snapshot_id, *snapshot.evidence_ids])
            ),
            risk_flags=[flag.value for flag in snapshot.risk_flags],
            model_call_ids=[],
            algorithm_version=CAPITAL_FLOW_ALGORITHM_VERSION,
            input_snapshot_hash=snapshot.input_snapshot_hash,
            shadow_mode=True,
            metadata=metadata,
        )
        return self.factor_repository.save(factor) if persist else factor

    async def analyze_factor_for_decision(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
    ) -> FactorOutput | None:
        try:
            response = await self.analyze(
                CapitalFlowAnalyzeRequest(
                    analysis_mode=AnalysisMode.DECISION,
                    data_cutoff=data_cutoff,
                    scope=CapitalFlowScope.SYMBOL,
                    symbol=symbol,
                    persist=True,
                )
            )
        except (KeyError, ValueError):
            return None
        result = response.result
        if not isinstance(result, CapitalFlowSymbolSnapshot):
            return None
        return self.factor_from_snapshot(result, persist=True)


class DecisionCapitalFlowService:
    """Decision integration returns only a shadow factor reference."""

    def __init__(
        self,
        service: CapitalFlowAnalysisService | None = None,
    ) -> None:
        self.service = service or CapitalFlowAnalysisService()

    async def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
    ) -> FactorOutput | None:
        return await self.service.analyze_factor_for_decision(
            symbol=symbol,
            data_cutoff=data_cutoff,
        )


__all__ = [
    "CapitalFlowAnalysisService",
    "DecisionCapitalFlowService",
    "ScreeningPerformance",
]
