from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def classify_tushare_error(exc: Exception) -> tuple[str, str]:
    """Map a provider exception without retaining its original message."""
    text = f"{type(exc).__name__}: {exc}".casefold()
    class_names = {
        cls.__name__.casefold() for cls in type(exc).__mro__
    }
    if class_names.intersection(
        {
            "connectionerror",
            "connecttimeout",
            "httperror",
            "networkerror",
            "proxyerror",
            "readtimeout",
            "requestexception",
            "sslerror",
            "timeout",
            "timeouterror",
        }
    ):
        return "NETWORK_ERROR", "Tushare network request failed"
    if any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "too many requests",
            "每分钟",
            "每小时",
            "访问频率",
            "频率限制",
            "限流",
        )
    ):
        return "RATE_LIMITED", "Tushare request was rate limited"
    if any(
        marker in text
        for marker in (
            "tushare_token 未配置",
            "token missing",
            "missing token",
            "token is required",
        )
    ):
        return "TOKEN_MISSING", "Tushare credential is not configured"
    if any(
        marker in text
        for marker in (
            "invalid token",
            "token invalid",
            "token无效",
            "token 无效",
            "认证失败",
            "authentication failed",
        )
    ):
        return "TOKEN_INVALID", "Tushare credential was rejected"
    if isinstance(exc, ValueError) or any(
        marker in text
        for marker in (
            "invalid parameter",
            "parameter error",
            "参数错误",
            "参数不正确",
            "格式不正确",
            "invalid date",
        )
    ):
        return "INVALID_PARAMETER", "Tushare request parameter is invalid"
    if any(
        marker in text
        for marker in (
            "permission denied",
            "not permitted",
            "没有访问该接口的权限",
            "没有权限",
            "权限不足",
            "积分不足",
        )
    ):
        return "PERMISSION_DENIED", "Tushare capability is not permitted"
    return "PROVIDER_ERROR", "Tushare provider request failed"


def _normalize_date(value: str) -> str:
    normalized = value.replace("-", "").strip()

    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"日期格式必须是 YYYYMMDD 或 YYYY-MM-DD：{value}")

    datetime.strptime(normalized, "%Y%m%d")
    return normalized


def _normalize_ts_code(value: str) -> str:
    normalized = value.strip().upper()

    if "." not in normalized:
        if normalized.startswith("920"):
            normalized = f"{normalized}.BJ"
        elif normalized.startswith(("5", "6", "9")):
            normalized = f"{normalized}.SH"
        else:
            normalized = f"{normalized}.SZ"

    code, exchange = normalized.split(".", 1)

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码：{value}")

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所：{exchange}")

    return f"{code}.{exchange}"


def _to_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _raw_value(value: object) -> object:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


class TushareProvider:
    """Tushare Pro structured-data adapter."""

    def __init__(self, token: str | None = None) -> None:
        resolved_token = token or settings.tushare_token

        if not resolved_token or not resolved_token.strip():
            raise RuntimeError("TUSHARE_TOKEN 未配置")

        import tushare as ts  # 延迟加载
        self._client = ts.pro_api(resolved_token.strip())

    def get_daily_by_trade_date(
        self,
        trade_date: date,
    ) -> list[dict[str, object]]:
        """Fetch one completed A-share trading day without unit conversion."""
        frame = self._client.daily(
            trade_date=trade_date.strftime("%Y%m%d"),
        )
        if frame.empty:
            return []
        required = {
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        }
        missing = required.difference(frame.columns)
        if missing:
            raise RuntimeError(
                "TUSHARE_DAILY_FIELDS_MISSING: "
                + ",".join(sorted(missing))
            )
        return [
            {
                str(column): _raw_value(value)
                for column, value in row.items()
            }
            for row in frame.to_dict(orient="records")
        ]

    @staticmethod
    def _frame_rows(
        frame: pd.DataFrame,
        *,
        required: set[str],
        capability: str,
    ) -> list[dict[str, object]]:
        if frame.empty:
            return []
        missing = required.difference(frame.columns)
        if missing:
            raise RuntimeError(
                f"{capability}_FIELDS_MISSING: "
                + ",".join(sorted(missing))
            )
        return [
            {
                str(column): _raw_value(value)
                for column, value in row.items()
            }
            for row in frame.to_dict(orient="records")
        ]

    def get_adjustment_factors(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        frame = self._client.adj_factor(
            ts_code=_normalize_ts_code(symbol),
            start_date=_normalize_date(start_date),
            end_date=_normalize_date(end_date),
        )
        return self._frame_rows(
            frame,
            required={"ts_code", "trade_date", "adj_factor"},
            capability="TUSHARE_ADJ_FACTOR",
        )

    def get_suspension_history(
        self,
        start_date: str,
        end_date: str,
        symbol: str | None = None,
    ) -> list[dict[str, object]]:
        parameters = {
            "start_date": _normalize_date(start_date),
            "end_date": _normalize_date(end_date),
        }
        if symbol is not None:
            parameters["ts_code"] = _normalize_ts_code(symbol)
        frame = self._client.suspend_d(**parameters)
        if frame.empty:
            return []
        if "ts_code" not in frame.columns or not (
            {"suspend_date"} <= set(frame.columns)
            or {"trade_date", "suspend_type"} <= set(frame.columns)
        ):
            raise RuntimeError(
                "TUSHARE_SUSPEND_D_FIELDS_MISSING: "
                "expected suspend_date or trade_date+suspend_type"
            )
        return [
            {
                str(column): _raw_value(value)
                for column, value in row.items()
            }
            for row in frame.to_dict(orient="records")
        ]

    def get_name_change_history(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        frame = self._client.namechange(
            start_date=_normalize_date(start_date),
            end_date=_normalize_date(end_date),
        )
        return self._frame_rows(
            frame,
            required={"ts_code", "name", "start_date"},
            capability="TUSHARE_NAMECHANGE",
        )

    def get_index_daily(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, object]]:
        code = index_code.strip().upper()
        if "." not in code:
            raise ValueError("index_code must include the provider exchange")
        frame = self._client.index_daily(
            ts_code=code,
            start_date=_normalize_date(start_date),
            end_date=_normalize_date(end_date),
        )
        return self._frame_rows(
            frame,
            required={"ts_code", "trade_date", "close"},
            capability="TUSHARE_INDEX_DAILY",
        )

    def get_sw_industry_indices(self) -> list[dict[str, object]]:
        frame = self._client.index_classify(
            level="L1",
            src="SW2021",
        )
        return self._frame_rows(
            frame,
            required={"index_code", "industry_name", "level", "src"},
            capability="TUSHARE_INDEX_CLASSIFY",
        )
    def get_index_members(
        self,
        industry_code: str,
    ) -> list[dict[str, object]]:
        frame = self._client.index_member_all(
            l1_code=industry_code.strip().upper(),
        )
        return self._frame_rows(
            frame,
            required={"ts_code", "l1_code", "in_date"},
            capability="TUSHARE_INDEX_MEMBER_ALL",
        )
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True,
    )
    def _fetch_daily(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        return self._client.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjustment_type: str = "RAW",
    ) -> list[MarketRecord]:
        if adjustment_type != "RAW":
            raise ValueError(
                "Tushare daily endpoint is RAW-only; adjusted history "
                "requires an independently verified adjustment-factor path"
            )
        ts_code = _normalize_ts_code(symbol)
        start = _normalize_date(start_date)
        end = _normalize_date(end_date)

        if start > end:
            raise ValueError("start_date 不能晚于 end_date")

        frame = self._fetch_daily(
            ts_code=ts_code,
            start_date=start,
            end_date=end,
        )

        if frame.empty:
            return []

        records: list[MarketRecord] = []

        for _, row in frame.sort_values("trade_date").iterrows():
            trade_date = str(row["trade_date"])

            payload: dict[str, object] = {
                "ts_code": str(row["ts_code"]),
                "trade_date": trade_date,
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "pre_close": _to_float(row.get("pre_close")),
                "change": _to_float(row.get("change")),
                "pct_chg": _to_float(row.get("pct_chg")),
                "vol": _to_float(row.get("vol")),
                "amount": _to_float(row.get("amount")),
                "adjustment_type": adjustment_type,
                "provider_adjustment": "daily",
                "volume_unit": "LOTS",
                "amount_unit": "THOUSAND_CNY",
            }

            hash_payload = {
                "source": "Tushare Pro",
                "symbol": ts_code,
                "data_type": DataType.DAILY_BAR.value,
                "trade_date": trade_date,
                "data": payload,
            }

            event_time = datetime.strptime(
                trade_date,
                "%Y%m%d",
            ).replace(
                hour=15,
                minute=0,
                second=0,
                tzinfo=SHANGHAI_TZ,
            )

            digest = _content_hash(hash_payload)
            records.append(
                MarketRecord(
                    record_id=f"raw_daily_{digest[:32]}",
                    symbol=ts_code,
                    data_type=DataType.DAILY_BAR,
                    event_time=event_time,
                    source_name="Tushare Pro",
                    source_level=SourceLevel.STRUCTURED,
                    verified=False,
                    content_hash=digest,
                    data=payload,
                )
            )

        return records


__all__ = ["TushareProvider", "classify_tushare_error"]
