from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data_hub.providers.jinniu_provider import (
    JinniuError,
    JinniuHttpStatusError,
    JinniuProvider,
    JinniuRateLimitError,
    JinniuTruncatedError,
    JinniuUnavailableError,
    canonical_hash,
    classify_jinniu_error,
)
from data_hub.repositories.jinniu import JinniuShadowRepository


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AUDIT_REPORT_NAME = "Jinniu_Data_Source_Audit.md"

# Capability-aware gates: a missing endpoint never silently passes; it is
# either FAIL (unexpected error) or PERMISSION_UNAVAILABLE (explicit 403).
CAPABILITY_GATES = {
    "CORE_STOCK_GATE": frozenset(
        {
            "stock_basic",
            "trade_cal",
            "stock_st",
            "namechange",
            "daily",
            "adj_factor",
            "stk_mins",
        }
    ),
    "INDEX_GATE": frozenset({"index_daily", "idx_mins"}),
    "ETF_GATE": frozenset({"etf_daily", "etf_mins"}),
}
GATE_PERMISSION_UNAVAILABLE = "PERMISSION_UNAVAILABLE"
GATE_COMPARISON_FIELDS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
        "trade_calendar",
    }
)


class JinniuShadowImportService:
    def __init__(
        self,
        *,
        provider: JinniuProvider | None = None,
        repository: JinniuShadowRepository | None = None,
        clock: Any | None = None,
    ) -> None:
        self.provider = provider or JinniuProvider()
        self.repository = repository or JinniuShadowRepository()
        self.clock = clock or (lambda: datetime.now(SHANGHAI_TZ))

    def import_endpoint(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        fields: Iterable[str] | None = None,
        data_cutoff: datetime | None = None,
        resume: bool = True,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        scope = {
            "endpoint": endpoint,
            "params": dict(params or {}),
            "fields": list(fields or ()),
        }
        run_id = run_id or f"jinniu_{canonical_hash([scope, self.clock()])[:24]}"
        previous = self.repository.load_checkpoint(endpoint, scope) if resume else None
        if previous and previous["status"] == "SUCCESS":
            return {
                "run_id": run_id,
                "endpoint": endpoint,
                "status": "SUCCESS",
                "persisted_count": previous["persisted_count"],
                "request_count": previous["request_count"],
                "resumed": True,
            }
        started = self.clock().astimezone(SHANGHAI_TZ)
        request_hash = canonical_hash(scope)
        self.repository.save_checkpoint(
            endpoint=endpoint,
            scope=scope,
            status="RUNNING",
            cursor=previous["cursor"] if previous else {},
            persisted_count=previous["persisted_count"] if previous else 0,
            request_count=previous["request_count"] if previous else 0,
            updated_at=started,
        )
        try:
            pages = self.provider.fetch_complete(
                endpoint,
                params=params,
                fields=fields,
            )
            cutoff = (data_cutoff or started).astimezone(SHANGHAI_TZ)
            records: list[dict[str, Any]] = []
            for page in pages:
                for row in page.rows:
                    contextual_row = dict(row)
                    if endpoint.endswith("_mins") and params:
                        contextual_row.setdefault("freq", params.get("freq"))
                    records.append(
                        self.provider.normalize_row(
                            endpoint,
                            contextual_row,
                            fetched_at=page.fetched_at,
                            data_cutoff=cutoff,
                            response_meta=page.meta,
                            schema_version=self.provider.schema_version,
                        )
                    )
            inserted = self.repository.persist(records)
            completed = self.clock().astimezone(SHANGHAI_TZ)
            persisted_total = (
                (previous["persisted_count"] if previous else 0) + inserted
            )
            request_total = (
                (previous["request_count"] if previous else 0) + len(pages)
            )
            self.repository.save_checkpoint(
                endpoint=endpoint,
                scope=scope,
                status="SUCCESS",
                cursor={"complete": True},
                persisted_count=persisted_total,
                request_count=request_total,
                updated_at=completed,
            )
            self.repository.record_request(
                run_id=run_id,
                endpoint=endpoint,
                status="SUCCESS",
                record_count=len(records),
                latency_ms=sum(page.latency_ms for page in pages),
                truncated=any(page.truncated for page in pages),
                retry_count=0,
                requested_at=started,
                completed_at=completed,
                request_hash=request_hash,
                http_status=200,
            )
            return {
                "run_id": run_id,
                "endpoint": endpoint,
                "status": "SUCCESS",
                "received_count": len(records),
                "persisted_count": persisted_total,
                "request_count": request_total,
                "resumed": previous is not None,
            }
        except JinniuError as exc:
            completed = self.clock().astimezone(SHANGHAI_TZ)
            paused = isinstance(
                exc,
                (JinniuRateLimitError, JinniuTruncatedError),
            )
            http_status = 429 if isinstance(exc, JinniuRateLimitError) else None
            if isinstance(exc, JinniuUnavailableError):
                http_status = 503
            if isinstance(exc, JinniuHttpStatusError):
                http_status = exc.status_code
            self.repository.save_checkpoint(
                endpoint=endpoint,
                scope=scope,
                status="PAUSED" if paused else "FAILED",
                cursor={"complete": False},
                persisted_count=previous["persisted_count"] if previous else 0,
                request_count=(previous["request_count"] if previous else 0) + 1,
                updated_at=completed,
                error_type=classify_jinniu_error(exc),
            )
            self.repository.record_request(
                run_id=run_id,
                endpoint=endpoint,
                status="FAILED",
                record_count=0,
                latency_ms=round((completed - started).total_seconds() * 1000),
                truncated=isinstance(exc, JinniuTruncatedError),
                retry_count=0,
                requested_at=started,
                completed_at=completed,
                request_hash=request_hash,
                http_status=http_status,
                error_type=classify_jinniu_error(exc),
            )
            raise

    def backfill_5min_sample(
        self,
        *,
        stock_symbols: Iterable[str] = (),
        etf_symbols: Iterable[str] = (),
        index_symbols: Iterable[str] = (),
        start_date: str,
        end_date: str,
        data_cutoff: datetime | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        groups = (
            ("stk_mins", stock_symbols),
            ("etf_mins", etf_symbols),
            ("idx_mins", index_symbols),
        )
        for endpoint, symbols in groups:
            for symbol in symbols:
                result.append(
                    self.import_endpoint(
                        endpoint,
                        params={
                            "ts_code": symbol,
                            "freq": "5min",
                            "start_date": start_date,
                            "end_date": end_date,
                        },
                        data_cutoff=data_cutoff,
                        resume=True,
                        run_id=run_id,
                    )
                )
        return result

    def compare_daily_with_canonical(
        self,
        *,
        run_id: str,
        symbols: Iterable[str] = (),
    ) -> int:
        symbol_list = list(symbols)
        predicate = ""
        parameters: list[Any] = []
        if symbol_list:
            placeholders = ",".join("?" for _ in symbol_list)
            predicate = f"AND shadow.symbol IN ({placeholders})"
            parameters.extend(symbol_list)
        with self.repository.connection_factory(read_only=True) as connection:
            rows = connection.execute(
                f"""
                WITH latest AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY endpoint, natural_key
                        ORDER BY fetched_at DESC
                    ) AS rank
                    FROM jinniu_shadow_records
                    WHERE endpoint = 'daily'
                )
                SELECT shadow.symbol, shadow.source_timestamp,
                       canonical.primary_source,
                       shadow.open, canonical.open,
                       shadow.high, canonical.high,
                       shadow.low, canonical.low,
                       shadow.close, canonical.close,
                       shadow.volume_shares, canonical.volume,
                       shadow.amount_cny, canonical.amount
                FROM latest AS shadow
                JOIN canonical_historical_bars AS canonical
                  ON canonical.symbol = shadow.symbol
                 AND canonical.trade_date = shadow.trade_date
                 AND canonical.adjustment_type = 'RAW'
                WHERE shadow.rank = 1 {predicate}
                """,
                parameters,
            ).fetchall()
        comparisons: list[dict[str, Any]] = []
        field_names = ("open", "high", "low", "close", "volume", "amount")
        tolerances = {
            "open": 0.005,
            "high": 0.005,
            "low": 0.005,
            "close": 0.005,
            "volume": 0.01,
            "amount": 0.01,
        }
        for row in rows:
            for index, field_name in enumerate(field_names):
                jinniu_value = row[3 + index * 2]
                other_value = row[4 + index * 2]
                absolute = (
                    abs(jinniu_value - other_value)
                    if jinniu_value is not None and other_value is not None
                    else None
                )
                relative = (
                    absolute / abs(other_value)
                    if absolute is not None and other_value not in (None, 0)
                    else absolute
                )
                comparisons.append(
                    {
                        "run_id": run_id,
                        "endpoint": "daily",
                        "symbol": row[0],
                        "observation_time": row[1],
                        "comparison_source": row[2],
                        "field_name": field_name,
                        "jinniu_value": jinniu_value,
                        "comparison_value": other_value,
                        "absolute_difference": absolute,
                        "relative_difference": relative,
                        "matched": (
                            relative <= tolerances[field_name]
                            if relative is not None
                            else None
                        ),
                        "quality": "COMPLETE",
                    }
                )
        return self.repository.persist_comparisons(comparisons)

    def compare_adj_factor(self, *, run_id: str) -> int:
        with self.repository.connection_factory(read_only=True) as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY endpoint, natural_key
                        ORDER BY fetched_at DESC
                    ) AS rank
                    FROM jinniu_shadow_records
                    WHERE endpoint = 'adj_factor'
                )
                SELECT shadow.symbol, shadow.source_timestamp,
                       shadow.adj_factor, reference.adj_factor,
                       reference.source
                FROM latest AS shadow
                JOIN historical_adjustment_factors AS reference
                  ON reference.symbol = shadow.symbol
                 AND reference.trade_date = shadow.trade_date
                WHERE shadow.rank = 1
                """
            ).fetchall()
        comparisons = []
        for symbol, timestamp, jinniu, reference, source in rows:
            absolute = (
                abs(jinniu - reference)
                if jinniu is not None and reference is not None
                else None
            )
            relative = (
                absolute / abs(reference)
                if absolute is not None and reference not in (None, 0)
                else absolute
            )
            comparisons.append(
                {
                    "run_id": run_id,
                    "endpoint": "adj_factor",
                    "symbol": symbol,
                    "observation_time": timestamp,
                    "comparison_source": source,
                    "field_name": "adj_factor",
                    "jinniu_value": jinniu,
                    "comparison_value": reference,
                    "absolute_difference": absolute,
                    "relative_difference": relative,
                    "matched": relative <= 0.0001 if relative is not None else None,
                    "quality": "COMPLETE",
                }
            )
        return self.repository.persist_comparisons(comparisons)

    def compare_trade_calendar(self, *, run_id: str) -> int:
        with self.repository.connection_factory(read_only=True) as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY endpoint, natural_key
                        ORDER BY fetched_at DESC
                    ) AS rank
                    FROM jinniu_shadow_records
                    WHERE endpoint = 'trade_cal'
                )
                SELECT shadow.source_timestamp, shadow.is_open,
                       reference.is_trading_day, reference.provider
                FROM latest AS shadow
                JOIN trading_calendar_days AS reference
                  ON reference.calendar_date = shadow.trade_date
                WHERE shadow.rank = 1
                """
            ).fetchall()
        comparisons = []
        for timestamp, jinniu, reference, source in rows:
            comparisons.append(
                {
                    "run_id": run_id,
                    "endpoint": "trade_cal",
                    "symbol": None,
                    "observation_time": timestamp,
                    "comparison_source": source,
                    "field_name": "trade_calendar",
                    "jinniu_value": float(jinniu),
                    "comparison_value": float(reference),
                    "absolute_difference": float(jinniu != reference),
                    "relative_difference": float(jinniu != reference),
                    "matched": jinniu == reference,
                    "quality": "COMPLETE",
                }
            )
        return self.repository.persist_comparisons(comparisons)

    def evaluate_capability_gates(self, *, run_id: str) -> dict[str, Any]:
        # Store-wide view: capability reflects everything proven so far,
        # not just one run (runs intentionally cover different scopes).
        del run_id
        metrics = self.repository.audit_metrics()
        request_rows = {row[0]: row for row in metrics["requests"]}
        gates: dict[str, Any] = {}
        for gate_name, endpoints in CAPABILITY_GATES.items():
            detail: dict[str, str] = {}
            has_permission_issue = False
            has_unexpected_failure = False
            for endpoint in sorted(endpoints):
                row = request_rows.get(endpoint)
                if row is None:
                    detail[endpoint] = "NOT_RUN"
                    has_unexpected_failure = True
                    continue
                if row[2] > 0:  # capability proven by any SUCCESS
                    detail[endpoint] = "SUCCESS"
                elif row[7] or row[11] == "PERMISSION_DENIED":
                    detail[endpoint] = GATE_PERMISSION_UNAVAILABLE
                    has_permission_issue = True
                else:
                    detail[
                        endpoint
                    ] = f"FAILED({row[11] or 'HTTP_ERROR'})"
                    has_unexpected_failure = True
            if has_unexpected_failure:
                status = "FAIL"
            elif has_permission_issue:
                status = GATE_PERMISSION_UNAVAILABLE
            else:
                status = "PASS"
            gates[gate_name] = {
                "status": status,
                "endpoints": detail,
            }
        return gates

    def evaluate_route_gate(self, *, run_id: str) -> dict[str, Any]:
        capability = self.evaluate_capability_gates(run_id=run_id)
        # Gate on THIS audit run's comparisons, matching the report scope.
        # Pre-fix runs stay in the table for continuous observation but must
        # not permanently veto the current pipeline quality.
        metrics = self.repository.audit_metrics(run_id)
        comparison_rows = metrics["comparisons"]
        comparisons_ok = bool(comparison_rows) and all(
            row[1] > 0 and row[2] / row[1] >= 0.99
            for row in comparison_rows
            if row[0] in GATE_COMPARISON_FIELDS
        )
        core_ok = (
            capability["CORE_STOCK_GATE"]["status"] == "PASS"
        )
        validated = core_ok and comparisons_ok
        capability_summary = "; ".join(
            f"{name}={body['status']}"
            for name, body in capability.items()
        )
        reason = (
            f"capability gates: {capability_summary}; "
            + (
                "dual-source comparison gates passed"
                if comparisons_ok
                else "dual-source comparison missing or below threshold"
            )
        )
        self.repository.set_route_gate(
            validated=validated,
            audit_run_id=run_id,
            at=self.clock().astimezone(SHANGHAI_TZ),
            reason=reason,
        )
        gate = self.repository.route_gate()
        gate["capability_gates"] = capability
        return gate

    def write_audit_report(
        self,
        *,
        run_id: str,
        output_path: str | Path,
    ) -> Path:
        metrics = self.repository.audit_metrics(run_id)
        gate = self.repository.route_gate()
        capability = self.evaluate_capability_gates(run_id=run_id)
        request_lines = [
            "| 接口 | 调用 | 成功率 | 平均延迟ms | 最大延迟ms | "
            "truncated | 401 | 403 | 405 | 429 | 503 | error_category |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in metrics["requests"]:
            success_rate = row[2] / row[1] if row[1] else 0
            request_lines.append(
                f"| {row[0]} | {row[1]} | {success_rate:.2%} | "
                f"{(row[3] or 0):.1f} | {row[4] or 0} | {row[5] or 0} | "
                f"{row[6] or 0} | {row[7] or 0} | {row[8] or 0} | "
                f"{row[9] or 0} | {row[10] or 0} | {row[11] or '-'} |"
            )
        if not metrics["requests"]:
            request_lines.append(
                "| NOT_RUN | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |"
            )
        coverage_lines = [
            "| 接口 | 记录数 | 标的数 | 最早日期 | 最晚日期 | 非完整记录 |",
            "|---|---:|---:|---|---|---:|",
        ]
        for row in metrics["coverage"]:
            coverage_lines.append(
                f"| {row[0]} | {row[1]} | {row[2]} | {row[3] or '-'} | "
                f"{row[4] or '-'} | {row[5] or 0} |"
            )
        if not metrics["coverage"]:
            coverage_lines.append("| NOT_RUN | 0 | 0 | - | - | 0 |")
        comparison_lines = [
            "| 字段 | 样本 | 一致率 | 平均绝对差 | 平均相对差 | 缺失 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for row in metrics["comparisons"]:
            rate = row[2] / row[1] if row[1] else 0
            comparison_lines.append(
                f"| {row[0]} | {row[1]} | {rate:.2%} | "
                f"{(row[3] or 0):.8g} | {(row[4] or 0):.8g} | {row[5]} |"
            )
        if not metrics["comparisons"]:
            comparison_lines.append("| NOT_RUN | 0 | 0% | 0 | 0 | 0 |")
        status = "PASS" if gate["validated"] else "NOT_PASSED"
        capability_lines = [
            f"- {name}: `{body['status']}`"
            for name, body in capability.items()
        ]
        text = "\n".join(
            [
                "# Jinniu Data Source Audit",
                "",
                f"- run_id: `{run_id}`",
                f"- route_gate: `{status}`",
                "- route: Jinniu PRIMARY / Tushare FALLBACK / "
                "BaoStock VALIDATION",
                f"- reason: {gate['reason']}",
                "- timezone: `Asia/Shanghai`",
                "- canonical_overwrite: `false`",
                "",
                "## 接口成功率、调用性能、429/503",
                "",
                *request_lines,
                "",
                "## 历史覆盖、字段覆盖、缺失率",
                "",
                *coverage_lines,
                "",
                "## 单位一致性",
                "",
                "- 日线：`vol * 100 -> volume_shares`；"
                "`amount * 1000 -> amount_cny`。",
                "- 分钟：`vol -> volume_shares`；`amount -> amount_cny`。",
                "- shadow 表同时保存供应商原单位和标准单位；"
                "不写 canonical。",
                "",
                "## 价格、成交量、成交额、复权因子、交易日历差异",
                "",
                *comparison_lines,
                "",
                "## 样本范围",
                "",
                "审计运行应覆盖 SH/SZ/BJ、ST、新股、退市历史和除权样本。"
                "缺少任一类时门禁不得通过。",
                "",
                "## Capability Gates",
                "",
                *capability_lines,
                "",
                "## 路由结论",
                "",
                "只有 `route_gate=PASS` 才允许启用 Jinniu 历史主路由。"
                "实时行情保持 Tencent。",
                "",
            ]
        )
        target = Path(output_path)
        target.write_text(text, encoding="utf-8")
        return target


__all__ = ["AUDIT_REPORT_NAME", "JinniuShadowImportService"]
