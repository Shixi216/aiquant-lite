from __future__ import annotations

import hashlib
import os
from datetime import datetime
from time import perf_counter

from trading.scanner.anomaly_detector import ScannerAnomalyDetector
from trading.scanner.evaluation import ScannerEvaluationService
from trading.scanner.feature_loader import ScannerFeatureLoader
from trading.scanner.query_parser import LocalChineseQueryParser, ParserModel
from trading.scanner.ranking import ScannerRanker
from trading.scanner.repository import ScannerRepository
from trading.scanner.result_cards import build_candidate_cards
from trading.scanner.schemas import (
    ScannerEvaluationRequest,
    ScannerEvaluationResponse,
    ScannerCandidateLayers,
    ScannerParseRequest,
    ScannerParseResponse,
    ScannerPerformance,
    ScannerQueryPlan,
    ScannerScanRequest,
    ScannerScanResponse,
)
from trading.scanner.universe_filter import UniverseFilter
from trading.schemas import AnalysisMode


def _process_peak_memory_bytes() -> int:
    """Read process peak RSS without tracemalloc's large pandas overhead."""

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.PeakWorkingSetSize) if ok else 0

    import resource

    maximum_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return maximum_rss if os.uname().sysname == "Darwin" else maximum_rss * 1024


class MarketScannerService:
    def __init__(
        self,
        repository: ScannerRepository | None = None,
        *,
        parser_model: ParserModel | None = None,
    ) -> None:
        self.repository = repository or ScannerRepository()
        self.parser = LocalChineseQueryParser(parser_model)
        self.loader = ScannerFeatureLoader(self.repository)
        self.detector = ScannerAnomalyDetector()
        self.ranker = ScannerRanker()
        self.evaluation = ScannerEvaluationService(self.repository)

    def parse(self, request: ScannerParseRequest) -> ScannerParseResponse:
        if request.data_cutoff > datetime.now().astimezone():
            raise ValueError("data_cutoff must not be in the future")
        return self.parser.parse(request)

    @staticmethod
    def _validate_mode(
        request: ScannerScanRequest,
        plan: ScannerQueryPlan,
    ) -> None:
        if plan.analysis_mode == AnalysisMode.RESEARCH and plan.top_n > 30:
            raise ValueError("RESEARCH accepts at most 30 candidates")
        if plan.analysis_mode == AnalysisMode.DECISION:
            if not request.explicit_decision_confirmation:
                raise ValueError(
                    "DECISION requires explicit user confirmation and symbol selection"
                )
            if not plan.include_symbols or len(plan.include_symbols) > 30:
                raise ValueError(
                    "DECISION handoff requires an explicit list of at most 30 symbols"
                )

    def _resolve_plan(
        self,
        request: ScannerScanRequest,
    ) -> tuple[ScannerQueryPlan | None, ScannerParseResponse]:
        if request.plan is not None:
            plan = request.plan
            response = ScannerParseResponse(
                parsed_query=plan,
                condition_summary=[
                    f"结构化计划：{len(plan.filters)}项硬条件",
                    f"返回：前{plan.top_n}只研究候选",
                ],
                clarification_required=bool(plan.ambiguity_flags),
                risk_flags=plan.ambiguity_flags,
            )
            return plan, response
        parsed = self.parse(
            ScannerParseRequest(
                query=request.query or "",
                analysis_mode=request.analysis_mode,
                data_cutoff=request.data_cutoff,
                top_n=request.top_n,
                allow_parser_model=request.allow_parser_model,
                missing_data_policy=request.missing_data_policy,
                freshness_policy=request.freshness_policy,
            )
        )
        return parsed.parsed_query, parsed

    def scan(
        self,
        request: ScannerScanRequest,
    ) -> ScannerScanResponse | ScannerParseResponse:
        if request.data_cutoff > datetime.now().astimezone():
            raise ValueError("data_cutoff must not be in the future")
        plan, parsed = self._resolve_plan(request)
        if plan is None or parsed.clarification_required:
            return parsed
        self._validate_mode(request, plan)

        started = perf_counter()
        features = self.loader.load(plan.data_cutoff)
        universe_count = len(features.frame)
        if universe_count == 0:
            raise RuntimeError("SCANNER_EMPTY_UNIVERSE")

        filter_started = perf_counter()
        filtered = UniverseFilter.apply(features.frame, plan)
        layers = UniverseFilter.partition(
            features.frame,
            plan,
            core_result=filtered,
        )
        filter_ms = (perf_counter() - filter_started) * 1000

        anomaly_started = perf_counter()
        detected = self.detector.detect(filtered.frame)
        if plan.anomaly_conditions:
            required = set(plan.anomaly_conditions)
            detected = detected[
                detected["anomaly_types"].map(
                    lambda values: required.issubset(set(values))
                )
            ].copy()
        anomaly_ms = (perf_counter() - anomaly_started) * 1000

        ranking_started = perf_counter()
        ranked = self.ranker.rank(detected, plan)
        ranking_ms = (perf_counter() - ranking_started) * 1000

        cards_started = perf_counter()
        cards = build_candidate_cards(ranked, limit=plan.top_n)
        near_ranked = self.ranker.rank(
            self.detector.detect(layers.near),
            plan,
        )
        control_ranked = self.ranker.rank(
            self.detector.detect(layers.control),
            plan,
        )
        if not control_ranked.empty:
            control_ranked = control_ranked.sort_values(
                by="amount",
                ascending=False,
                na_position="last",
                kind="stable",
            )
        near_cards = build_candidate_cards(
            near_ranked,
            limit=plan.top_n,
            candidate_layer="NEAR",
            layer_reason="仅接近一项现有条件，仅供观察",
        )
        control_cards = build_candidate_cards(
            control_ranked,
            limit=plan.top_n,
            candidate_layer="CONTROL",
            layer_reason="高成交但未命中现有条件，仅作对照",
        )
        card_ms = (perf_counter() - cards_started) * 1000
        elapsed_ms = (perf_counter() - started) * 1000
        peak = _process_peak_memory_bytes()

        identity = hashlib.sha256(
            (
                plan.plan_hash
                + features.input_snapshot_hash
                + plan.analysis_mode.value
            ).encode()
        ).hexdigest()
        coverage = {f"{index}/5": 0 for index in range(6)}
        for card in cards:
            coverage[card.factor_coverage] += 1
        stale = bool(
            features.frame["snapshot_stale"].all()
            if not features.frame.empty
            else True
        )
        response = ScannerScanResponse(
            run_id="sr_" + identity[:24],
            parsed_query=plan,
            condition_summary=parsed.condition_summary,
            analysis_mode=plan.analysis_mode,
            data_cutoff=plan.data_cutoff,
            snapshot_time=features.snapshot_time,
            stale=stale,
            universe_count=universe_count,
            scanned_count=universe_count,
            matched_count=len(ranked),
            returned_count=len(cards),
            no_match_reason=(
                None
                if cards
                else (
                    "当前本地行业分类中没有匹配请求关键词的股票；"
                    "未改用概念标签或模型推断。"
                    if plan.include_industries
                    else "没有股票同时满足全部显式条件。"
                )
            ),
            candidates=cards,
            candidate_layers=ScannerCandidateLayers(
                core=cards,
                near=near_cards,
                control=control_cards,
            ),
            performance=ScannerPerformance(
                cold_cache=features.cold_cache,
                elapsed_ms=elapsed_ms,
                data_read_ms=features.data_read_ms,
                feature_compute_ms=features.feature_compute_ms,
                filter_ms=filter_ms,
                anomaly_ms=anomaly_ms,
                ranking_ms=ranking_ms,
                result_card_ms=card_ms,
                peak_memory_bytes=peak,
                database_session_count=features.database_session_count,
                database_query_count=features.database_query_count,
            ),
            factor_coverage_summary=coverage,
            missing_data_summary=filtered.missing_data_summary,
            input_snapshot_hash=features.input_snapshot_hash,
            persisted=request.persist_run,
        )
        if request.persist_run:
            self.repository.save_scan(response)
        return response

    def research_handoff(
        self,
        response: ScannerScanResponse,
        *,
        ai_deep_analysis_limit: int = 10,
    ) -> dict[str, object]:
        if len(response.candidates) > 30:
            raise ValueError("RESEARCH accepts at most 30 candidates")
        if not 0 <= ai_deep_analysis_limit <= 10:
            raise ValueError("AI deep analysis limit must be between 0 and 10")
        return {
            "analysis_mode": AnalysisMode.RESEARCH.value,
            "symbols": [
                item.symbol for item in response.candidates[:30]
            ],
            "ai_deep_analysis_symbols": [
                item.symbol
                for item in response.candidates[:ai_deep_analysis_limit]
            ],
            "network_fetch_started": False,
            "decision_called": False,
            "is_trade_recommendation": False,
        }

    def evaluate(
        self,
        request: ScannerEvaluationRequest,
    ) -> ScannerEvaluationResponse:
        return self.evaluation.evaluate(request)

    def run_detail(self, run_id: str) -> dict[str, object] | None:
        return self.repository.run_detail(run_id)

    def run_candidates(self, run_id: str) -> list[dict[str, object]]:
        return self.repository.run_candidates(run_id)

    def symbol(self, symbol: str) -> dict[str, object] | None:
        return self.repository.latest_symbol_candidate(symbol)


__all__ = ["MarketScannerService"]
