from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config.network import configure_network_policy
from config.settings import settings
from database.db import (
    get_connection,
    initialize_database,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.service import (
    FinancialStatementResponse,
    ProviderRun,
)
from data_hub.repositories.unified import _financial_metadata
from data_hub.services.canonicalization_service import CanonicalizationService


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

STATEMENTS = {
    "income": "利润表",
    "balancesheet": "资产负债表",
    "cashflow": "现金流量表",
}


def _normalize_symbol(value: str) -> str:
    normalized = value.strip().upper()
    code = normalized.split(".", 1)[0]

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码：{value}")

    if "." in normalized:
        exchange = normalized.split(".", 1)[1]
    elif code.startswith(("5", "6", "9")):
        exchange = "SH"
    elif code.startswith(("4", "8")):
        exchange = "BJ"
    else:
        exchange = "SZ"

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所：{exchange}")

    return f"{code}.{exchange}"


def _normalize_date(value: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"日期格式必须是 YYYYMMDD：{value}")

    datetime.strptime(normalized, "%Y%m%d")
    return normalized


def _clean_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, pd.Timestamp):
        return value.isoformat()

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return int(value)

    if isinstance(value, float):
        if math.isnan(value):
            return None
        return float(value)

    if hasattr(value, "item"):
        try:
            return _clean_value(value.item())
        except (TypeError, ValueError):
            pass

    return str(value)


def _event_time(row: pd.Series) -> datetime:
    for field in ("f_ann_date", "ann_date", "end_date"):
        value = _clean_value(row.get(field))

        if value:
            normalized = str(value).replace("-", "")

            if len(normalized) == 8 and normalized.isdigit():
                return datetime.strptime(
                    normalized,
                    "%Y%m%d",
                ).replace(
                    hour=(
                        settings
                        .fundamental_disclosure_date_available_hour
                    ),
                    minute=0,
                    second=0,
                    tzinfo=SHANGHAI_TZ,
                )

    return datetime.now(tz=SHANGHAI_TZ)


def _content_hash(
    source_name: str,
    symbol: str,
    statement_type: str,
    report_period: str,
    payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "source": source_name,
            "symbol": symbol,
            "statement_type": statement_type,
            "report_period": report_period,
            "data": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FinancialStatementService:
    """Fetch and persist period-consistent financial statements."""

    def __init__(self) -> None:
        configure_network_policy()

        if not settings.tushare_token:
            raise RuntimeError("TUSHARE_TOKEN 未配置")

        import tushare as ts  # 延迟加载
        self._client = ts.pro_api(settings.tushare_token.strip())

    def _run_statement(
        self,
        statement_type: str,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> tuple[pd.DataFrame | None, ProviderRun]:
        started = perf_counter()
        provider_name = (
            f"Tushare Pro / {STATEMENTS[statement_type]}"
        )

        try:
            fetcher = getattr(self._client, statement_type)

            frame = fetcher(
                ts_code=symbol,
                start_date=start_date,
                end_date=end_date,
            )

            latency_ms = round(
                (perf_counter() - started) * 1000
            )

            if frame.empty:
                raise RuntimeError(
                    f"{STATEMENTS[statement_type]}返回空数据"
                )

            if "end_date" not in frame.columns:
                raise RuntimeError(
                    f"{STATEMENTS[statement_type]}缺少 end_date"
                )

            return frame, ProviderRun(
                provider=provider_name,
                success=True,
                record_count=len(frame),
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = round(
                (perf_counter() - started) * 1000
            )

            return None, ProviderRun(
                provider=provider_name,
                success=False,
                record_count=0,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    @staticmethod
    def _select_row(
        frame: pd.DataFrame,
        report_period: str,
    ) -> pd.Series:
        selected = frame[
            frame["end_date"].astype(str) == report_period
        ].copy()

        if selected.empty:
            raise RuntimeError(
                f"未找到报告期 {report_period}"
            )

        sort_columns = [
            column
            for column in (
                "update_flag",
                "f_ann_date",
                "ann_date",
            )
            if column in selected.columns
        ]

        if sort_columns:
            selected = selected.sort_values(
                sort_columns,
                ascending=[False] * len(sort_columns),
                na_position="last",
            )

        return selected.iloc[0]

    @staticmethod
    def _build_record(
        symbol: str,
        statement_type: str,
        report_period: str,
        row: pd.Series,
    ) -> MarketRecord:
        payload = {
            str(key): _clean_value(value)
            for key, value in row.to_dict().items()
        }

        payload["statement_type"] = statement_type
        payload["statement_name"] = STATEMENTS[statement_type]
        payload["report_period"] = report_period
        announcement_time = None
        for field in ("f_ann_date", "ann_date"):
            value = payload.get(field)
            if value:
                normalized = str(value).replace("-", "")
                if len(normalized) == 8 and normalized.isdigit():
                    announcement_time = _event_time(row).isoformat()
                    break
        payload["announcement_time"] = announcement_time
        payload["data_available_time"] = announcement_time

        source_name = (
            f"Tushare Pro / {STATEMENTS[statement_type]}"
        )

        return MarketRecord(
            symbol=symbol,
            data_type=DataType.FINANCIAL_STATEMENT,
            event_time=_event_time(row),
            source_name=source_name,
            source_level=SourceLevel.STRUCTURED,
            verified=False,
            content_hash=_content_hash(
                source_name=source_name,
                symbol=symbol,
                statement_type=statement_type,
                report_period=report_period,
                payload=payload,
            ),
            data=payload,
        )

    @staticmethod
    def _persist(records: list[MarketRecord]) -> None:
        initialize_database()
        if not records:
            return
        hashes = [record.content_hash for record in records if record.content_hash]
        persisted_by_hash: dict[str, str] = {}
        with get_connection() as connection:
            persisted_by_hash = {
                row[0]: row[1]
                for row in connection.execute(
                    """
                    SELECT content_hash, record_id
                    FROM data_records
                    WHERE content_hash IN (
                        SELECT UNNEST(?::VARCHAR[])
                    )
                    """,
                    [hashes],
                ).fetchall()
                if row[0] is not None
            }
            missing = [
                record
                for record in records
                if record.content_hash not in persisted_by_hash
            ]
            if missing:
                view = "_financial_raw_records"
                connection.register(
                    view,
                    pd.DataFrame.from_records(
                        [
                            {
                                "record_id": record.record_id,
                                "task_id": None,
                                "symbol": record.symbol,
                                "data_type": record.data_type.value,
                                "event_time": record.event_time,
                                "fetched_at": record.fetched_at,
                                "source_name": record.source_name,
                                "source_url": (
                                    str(record.source_url)
                                    if record.source_url
                                    else None
                                ),
                                "source_level": record.source_level.value,
                                "verified": record.verified,
                                "content_hash": record.content_hash,
                                "payload_json": json.dumps(
                                    record.data,
                                    ensure_ascii=False,
                                    default=str,
                                ),
                            }
                            for record in missing
                        ]
                    ),
                )
                try:
                    connection.execute(
                        """
                        INSERT INTO data_records (
                            record_id, task_id, symbol, data_type,
                            event_time, fetched_at, source_name,
                            source_url, source_level, verified,
                            content_hash, payload_json
                        )
                        SELECT record_id, task_id, symbol, data_type,
                               event_time, fetched_at, source_name,
                               source_url, source_level, verified,
                               content_hash, payload_json
                        FROM _financial_raw_records
                        ON CONFLICT DO NOTHING
                        """
                    )
                finally:
                    connection.unregister(view)
                persisted_by_hash.update(
                    {
                        record.content_hash: record.record_id
                        for record in missing
                        if record.content_hash is not None
                    }
                )

        resolved = [
            record.model_copy(
                update={
                    "record_id": persisted_by_hash.get(
                        record.content_hash,
                        record.record_id,
                    )
                }
            )
            for record in records
        ]
        canonicalizer = CanonicalizationService(
            initialize_repositories=False
        )
        canonical = [
            canonicalizer.canonicalize_financial(
                [record],
                generated_at=record.fetched_at,
                persist=False,
            )
            for record in resolved
        ]
        rows: list[dict[str, Any]] = []
        for item in canonical:
            metadata = _financial_metadata(item.payload)
            rows.append(
                {
                    "canonical_record_id": item.canonical_record_id,
                    "symbol": item.symbol,
                    "data_type": item.data_type,
                    "event_time": item.event_time,
                    "data_cutoff": item.data_cutoff,
                    "generated_at": item.generated_at,
                    "primary_source": item.primary_source,
                    "source_record_ids_json": json.dumps(
                        item.source_record_ids,
                        ensure_ascii=False,
                    ),
                    "verification_source_ids_json": json.dumps(
                        item.verification_source_ids,
                        ensure_ascii=False,
                    ),
                    "verification_status": item.verification_status.value,
                    "field_differences_json": json.dumps(
                        item.field_differences,
                        ensure_ascii=False,
                        default=str,
                    ),
                    "payload_json": json.dumps(
                        item.payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                    "confidence": item.confidence,
                    "content_hash": item.content_hash,
                    "algorithm_version": item.algorithm_version,
                    "report_period": metadata["report_period"],
                    "announcement_time": metadata["announcement_time"],
                    "data_available_time": metadata["data_available_time"],
                    "statement_type": metadata["statement_type"],
                    "statement_version": metadata["statement_version"],
                    "revision_of_record_id": None,
                    "accounting_scope": metadata["accounting_scope"],
                    "period_type": metadata["period_type"],
                    "source_type": metadata["source_type"],
                    "disclosure_time_source": metadata[
                        "disclosure_time_source"
                    ],
                }
            )
        with get_connection() as connection:
            view = "_financial_canonical_records"
            frame = pd.DataFrame.from_records(rows)
            connection.register(view, frame)
            columns = list(frame.columns)
            quoted = ", ".join(f'"{column}"' for column in columns)
            updates = ", ".join(
                f'"{column}" = excluded."{column}"'
                for column in columns
                if column not in {
                    "canonical_record_id",
                    "symbol",
                    "data_type",
                    "event_time",
                    "revision_of_record_id",
                }
            )
            try:
                connection.execute(
                    f'''
                    INSERT INTO canonical_financial_records ({quoted})
                    SELECT {quoted}
                    FROM "{view}"
                    ON CONFLICT (canonical_record_id) DO UPDATE SET
                        {updates}
                    '''
                )
            finally:
                connection.unregister(view)

    @classmethod
    def _build_history_records(
        cls,
        *,
        symbol: str,
        frames: dict[str, pd.DataFrame],
    ) -> list[MarketRecord]:
        period_sets = [
            set(frame["end_date"].dropna().astype(str).tolist())
            for frame in frames.values()
        ]
        if not period_sets:
            return []
        common_periods = sorted(set.intersection(*period_sets))
        records: list[MarketRecord] = []
        for report_period in common_periods:
            for statement_type, frame in frames.items():
                row = cls._select_row(
                    frame=frame,
                    report_period=report_period,
                )
                records.append(
                    cls._build_record(
                        symbol=symbol,
                        statement_type=statement_type,
                        report_period=report_period,
                        row=row,
                    )
                )
        return records

    def get_financial_statement_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        persist: bool = True,
    ) -> list[MarketRecord]:
        """Fetch every period shared by the available statement endpoints."""

        canonical_symbol = _normalize_symbol(symbol)
        start = _normalize_date(start_date)
        end = _normalize_date(end_date)
        if start > end:
            raise ValueError("start_date must not be later than end_date")
        frames: dict[str, pd.DataFrame] = {}
        provider_runs: list[ProviderRun] = []
        for statement_type in STATEMENTS:
            frame, run = self._run_statement(
                statement_type=statement_type,
                symbol=canonical_symbol,
                start_date=start,
                end_date=end,
            )
            provider_runs.append(run)
            if frame is not None:
                frames[statement_type] = frame
        if not frames:
            raise RuntimeError("all financial statement providers failed")
        records = self._build_history_records(
            symbol=canonical_symbol,
            frames=frames,
        )
        if not records:
            raise RuntimeError(
                "financial statements have no common historical report period"
            )
        if persist:
            self._persist(records)
        return records
    def get_financial_statement(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        persist: bool = True,
    ) -> FinancialStatementResponse:
        canonical_symbol = _normalize_symbol(symbol)
        start = _normalize_date(start_date)
        end = _normalize_date(end_date)

        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        frames: dict[str, pd.DataFrame] = {}
        provider_runs: list[ProviderRun] = []

        for statement_type in STATEMENTS:
            frame, run = self._run_statement(
                statement_type=statement_type,
                symbol=canonical_symbol,
                start_date=start,
                end_date=end,
            )

            provider_runs.append(run)

            if frame is not None:
                frames[statement_type] = frame

        if not frames:
            errors = "; ".join(
                f"{run.provider}: {run.error_message}"
                for run in provider_runs
            )
            raise RuntimeError(
                f"所有财务报表接口均失败：{errors}"
            )

        period_sets = [
            set(
                frame["end_date"]
                .dropna()
                .astype(str)
                .tolist()
            )
            for frame in frames.values()
        ]

        common_periods = set.intersection(*period_sets)

        if not common_periods:
            raise RuntimeError(
                "成功返回的财务报表不存在共同报告期，"
                "已拒绝混合不同期间的数据"
            )

        report_period = max(common_periods)
        records: list[MarketRecord] = []

        for statement_type, frame in frames.items():
            row = self._select_row(
                frame=frame,
                report_period=report_period,
            )

            records.append(
                self._build_record(
                    symbol=canonical_symbol,
                    statement_type=statement_type,
                    report_period=report_period,
                    row=row,
                )
            )

        missing_statements = [
            statement_type
            for statement_type in STATEMENTS
            if statement_type not in frames
        ]

        if persist:
            self._persist(records)

        return FinancialStatementResponse(
            symbol=canonical_symbol,
            report_period=report_period,
            records=records,
            provider_runs=provider_runs,
            complete=not missing_statements,
            missing_statements=missing_statements,
        )
