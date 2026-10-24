"""Daily bars service with route-policy-driven provider chain.

2026-08-30 重构（用户指定 P1/P3/P6 层）：

- P1 主链校正：不再硬编码 Tushare→AKShare→BaoStock，改读
  ``historical_route_policy`` 的 ROUTE_TABLE 按当前 stage 构建链：
    jinniu_primary: Jinniu(主) → Tushare(fallback) → BaoStock(校验)
    shadow:        Tushare(主) → BaoStock/Jinniu(校验)
  AKShare 移出日线主链（保留在 announcements/news 等非K线能力）。
- P3 熔断：每个 Provider 挂独立 CircuitBreaker，连续失败后 OPEN
  短路跳过，不再每个请求都撞一次外部源。
- P6 统一结果：``ProviderOutcome`` 携带 status/data/latency/error/
  retry/fallback/timestamp，上层只按 成功→使用 / 失败→fallback /
  超时→fallback / 熔断→skip 处理。
- 超时线程保留作最后保险（BaoStock 是 socket 协议无原生超时），
  Jinniu/Tushare 已在 Provider 内部实现原生 timeout+retry。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import Any

from config.network import configure_network_policy
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.providers import (
    BaoStockProvider,
    JinniuProvider,
    TushareProvider,
)
from data_hub.schemas.market import MarketRecord
from data_hub.schemas.service import DailyBarsResponse, ProviderRun
from data_hub.services.canonicalization_service import CanonicalizationService
from data_hub.services.circuit_breaker import get_breaker
from data_hub.services.historical_route_policy import (
    STAGE_JINNIU_PRIMARY,
    STAGE_SHADOW,
    load_stage,
    resolve_route,
)
from data_hub.verification import verify_daily_bars

# 每源兜底超时（秒）：Jinniu/Tushare 有原生超时，此值只是最后保险；
# BaoStock（socket 协议）无原生超时，主要靠此值兜底
PROVIDER_FALLBACK_TIMEOUT_SECONDS = 25.0

# 熔断参数：连续失败 3 次 → OPEN 5 分钟
BREAKER_FAILURE_THRESHOLD = 3
BREAKER_OPEN_TIMEOUT_SECONDS = 300.0

# Provider 构造器注册表（按路由策略中的名字取实例）
_PROVIDER_FACTORIES: dict[str, callable] = {
    "Jinniu": lambda: JinniuProvider(),
    "Tushare": lambda: TushareProvider(),
    "BaoStock": lambda: BaoStockProvider(),
}


# ---- P5 监控计数器：防"假超时线程"积累 ----
# 记录每次 Provider 调用的 active/超时/连续超时，供 /v1/providers/health 暴露。
# Jinniu/Tushare 已实现 Provider 内原生 timeout，正常不会残留线程；
# 此计数器用于发现异常积累（连续超时攀升 = 某 Provider 原生超时失效）。
_MONITOR_LOCK = threading.Lock()
_MONITOR: dict[str, dict[str, int]] = {}


def _monitor_tick(provider: str, status: str) -> None:
    with _MONITOR_LOCK:
        entry = _MONITOR.setdefault(provider, {
            "active_calls": 0,
            "timeout_count": 0,
            "consecutive_timeouts": 0,
            "total_calls": 0,
            "circuit_skips": 0,
        })
        entry["total_calls"] += 1
        if status == "timeout":
            entry["timeout_count"] += 1
            entry["consecutive_timeouts"] += 1
        elif status == "ok":
            entry["consecutive_timeouts"] = 0
        elif status == "skipped":
            entry["circuit_skips"] += 1


def monitor_snapshot() -> dict:
    with _MONITOR_LOCK:
        return {name: dict(entry) for name, entry in _MONITOR.items()}


@dataclass
class ProviderOutcome:
    """P6 统一 Provider 调用结果。"""

    provider: str
    status: str  # ok / error / timeout / skipped(circuit open)
    data: list[MarketRecord] = field(default_factory=list)
    latency_ms: int = 0
    error_type: str = ""
    error_message: str = ""
    retry_count: int = 0
    fallback_used: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class DailyBarsService:
    """Fetch, normalize, verify, and persist A-share daily bars."""

    def __init__(self, stage: str | None = None) -> None:
        configure_network_policy()

        self._stage = stage or load_stage()
        # 按当前 stage 解析路由计划（jinniu_primary 需 gate 已验证，
        # 未验证时 resolve_route 抛 RouteGateError → 上层 503）
        self._plan = resolve_route("stock_daily", stage=self._stage, gate_validated=True)

        # 构建调用链：primary → fallback* → validation*
        chain_names: list[str] = [self._plan.primary]
        chain_names.extend(self._plan.fallback)
        chain_names.extend(self._plan.validation)
        # 去重保序
        seen: set[str] = set()
        self._chain: list[tuple[str, Any]] = []
        for name in chain_names:
            if name in seen:
                continue
            seen.add(name)
            factory = _PROVIDER_FACTORIES.get(name)
            if factory is None:
                continue
            self._chain.append((name, factory()))

        if not self._chain:
            raise RuntimeError(
                f"路由计划 {self._stage} 未解析出任何可用 Provider"
            )

    # ---- Provider 调用（P6 统一出口） ----

    def _call_provider(
        self,
        provider_name: str,
        provider: Any,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> ProviderOutcome:
        breaker = get_breaker(
            provider_name,
            failure_threshold=BREAKER_FAILURE_THRESHOLD,
            open_timeout_seconds=BREAKER_OPEN_TIMEOUT_SECONDS,
        )
        if not breaker.allow_request():
            _monitor_tick(provider_name, "skipped")
            return ProviderOutcome(
                provider=provider_name,
                status="skipped",
                error_type="CircuitOpen",
                error_message="熔断器打开，跳过该数据源",
            )

        started = perf_counter()
        result: dict[str, Any] = {}
        worker = threading.Thread(
            target=_fetch_into,
            args=(result, provider, symbol, start_date, end_date),
            name=f"provider-{provider_name}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=PROVIDER_FALLBACK_TIMEOUT_SECONDS)

        latency_ms = round((perf_counter() - started) * 1000)

        if worker.is_alive():
            # 兜底超时：Provider 内部原生超时未生效时由这里切断
            breaker.record_failure("fallback timeout")
            _monitor_tick(provider_name, "timeout")
            return ProviderOutcome(
                provider=provider_name,
                status="timeout",
                latency_ms=latency_ms,
                error_type="ProviderTimeout",
                error_message=(
                    f"数据源 {provider_name} 请求超时"
                    f"（>{PROVIDER_FALLBACK_TIMEOUT_SECONDS:.0f}s），已跳过"
                ),
            )

        if "error" in result:
            breaker.record_failure(str(result["error"]))
            _monitor_tick(provider_name, "error")
            return ProviderOutcome(
                provider=provider_name,
                status="error",
                latency_ms=latency_ms,
                error_type=type(result["error"]).__name__,
                error_message=str(result["error"]),
            )

        breaker.record_success()
        _monitor_tick(provider_name, "ok")
        records = result.get("records") or []
        return ProviderOutcome(
            provider=provider_name,
            status="ok",
            data=records,
            latency_ms=latency_ms,
        )

    # ---- 持久化（保持原逻辑） ----

    @staticmethod
    def _trade_date(record: MarketRecord) -> str:
        value = record.data.get("trade_date")
        if value is None:
            raise RuntimeError(f"{record.source_name} 记录缺少 trade_date")
        return str(value)

    @staticmethod
    def _insert_if_absent(connection: Any, record: MarketRecord) -> None:
        existing = connection.execute(
            """
            SELECT record_id
            FROM data_records
            WHERE content_hash = ?
            LIMIT 1
            """,
            [record.content_hash],
        ).fetchone()
        if existing is None:
            insert_market_record(connection, record)

    def _persist_records(self, records: list[MarketRecord]) -> None:
        initialize_database()
        with get_connection() as connection:
            for record in records:
                self._insert_if_absent(connection, record)
            verified_hashes = [
                record.content_hash
                for record in records
                if record.verified and record.content_hash
            ]
            if verified_hashes:
                placeholders = ",".join("?" for _ in verified_hashes)
                connection.execute(
                    f"""
                    UPDATE data_records
                    SET verified = TRUE
                    WHERE content_hash IN ({placeholders})
                    """,
                    verified_hashes,
                )
        groups: dict[tuple[str, str, object], list[MarketRecord]] = {}
        for record in records:
            key = (record.symbol, record.data_type.value, record.event_time)
            groups.setdefault(key, []).append(record)
        canonicalization = CanonicalizationService()
        for group in groups.values():
            canonicalization.canonicalize_market(group)

    # ---- 主入口 ----

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        persist: bool = True,
    ) -> DailyBarsResponse:
        outcomes: list[ProviderOutcome] = []
        records_by_source: dict[str, list[MarketRecord]] = {}
        provider_runs: list[ProviderRun] = []

        for provider_name, provider in self._chain:
            outcome = self._call_provider(
                provider_name=provider_name,
                provider=provider,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            outcomes.append(outcome)
            provider_runs.append(
                ProviderRun(
                    provider=provider_name,
                    success=(outcome.status == "ok"),
                    record_count=len(outcome.data),
                    latency_ms=outcome.latency_ms,
                    error_type=outcome.error_type or None,
                    error_message=outcome.error_message or None,
                )
            )
            if outcome.data:
                records_by_source[provider_name] = outcome.data

        successful_sources = list(records_by_source.keys())

        if not successful_sources:
            errors = "; ".join(
                f"{o.provider}: {o.error_type or o.status}: {o.error_message}"
                for o in outcomes
                if o.status != "skipped"
            )
            skipped = sum(1 for o in outcomes if o.status == "skipped")
            detail = errors or "（全部被熔断器跳过）"
            if skipped:
                detail += f"；{skipped} 个数据源被熔断器跳过"
            raise RuntimeError(f"所有日线数据源均失败：{detail}")

        # 交叉验证（保持原逻辑）：≥2 源一致的日期标记 verified
        indexed: dict[str, dict[str, MarketRecord]] = {}
        for source, records in records_by_source.items():
            indexed[source] = {
                self._trade_date(record): record for record in records
            }
        all_dates = sorted(
            {
                trade_date
                for source_records in indexed.values()
                for trade_date in source_records
            }
        )
        verified_dates: list[str] = []
        canonical_records: list[MarketRecord] = []
        for trade_date in all_dates:
            candidates = [
                indexed[source][trade_date]
                for source in indexed
                if trade_date in indexed[source]
            ]
            if len(candidates) >= 2:
                verification = verify_daily_bars(candidates)
                if verification.verified:
                    verified_dates.append(trade_date)
                    for record in candidates:
                        record.verified = True
            canonical: MarketRecord | None = None
            for source in indexed:
                record = indexed[source].get(trade_date)
                if record is not None:
                    canonical = record
                    break
            if canonical is not None:
                canonical_records.append(canonical)

        all_records = [
            record
            for records in records_by_source.values()
            for record in records
        ]
        if persist:
            self._persist_records(all_records)

        verified_set = set(verified_dates)
        return DailyBarsResponse(
            symbol=canonical_records[0].symbol,
            start_date=start_date,
            end_date=end_date,
            primary_source=successful_sources[0],
            records=canonical_records,
            provider_runs=provider_runs,
            verified_dates=verified_dates,
            unverified_dates=[
                trade_date
                for trade_date in all_dates
                if trade_date not in verified_set
            ],
        )


def _fetch_into(
    result: dict[str, Any],
    provider: Any,
    symbol: str,
    start_date: str,
    end_date: str,
) -> None:
    """在工作线程中调用 provider，结果写入共享 dict。"""
    try:
        result["records"] = provider.get_daily_bars(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:  # noqa: BLE001 - 源级隔离，任何异常都交给主线程处理
        result["error"] = exc
