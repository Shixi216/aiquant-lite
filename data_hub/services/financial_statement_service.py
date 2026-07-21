from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts

from config.network import configure_network_policy
from config.settings import settings
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.service import (
    FinancialStatementResponse,
    ProviderRun,
)


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
                    hour=18,
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

        with get_connection() as connection:
            for record in records:
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