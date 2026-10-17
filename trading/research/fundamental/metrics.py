from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from trading.research.fundamental.models import (
    FundamentalMetrics,
    FundamentalRiskFlag,
    MarketValuationPoint,
    PointInTimeFinancialRecord,
)


FUNDAMENTAL_METRICS_VERSION = "fundamental-metrics-v2-official-roe"
CORE_METRIC_FIELDS = (
    "pe_ttm",
    "pb",
    "roe",
    "revenue_growth",
    "net_profit_growth",
    "debt_ratio",
    "operating_cash_flow",
    "peg",
)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _ratio(
    numerator: Any,
    denominator: Any,
    risk_flags: set[FundamentalRiskFlag],
) -> float | None:
    left = _number(numerator)
    right = _number(denominator)
    if left is None or right is None:
        return None
    if right == 0:
        risk_flags.add(FundamentalRiskFlag.INVALID_DENOMINATOR)
        return None
    return left / right


def _period_key(value: str | None) -> str:
    return (value or "").replace("-", "")


def _latest_statement_set(
    records: list[PointInTimeFinancialRecord],
) -> tuple[
    str | None,
    dict[str, PointInTimeFinancialRecord],
    list[PointInTimeFinancialRecord],
]:
    periods = sorted(
        {_period_key(record.report_period) for record in records}
        - {""},
        reverse=True,
    )
    if not periods:
        return None, {}, records
    selected_period = periods[0]
    current: dict[str, PointInTimeFinancialRecord] = {}
    for record in records:
        if _period_key(record.report_period) != selected_period:
            continue
        statement_type = record.statement_type or record.data_type.rsplit(":", 1)[-1]
        existing = current.get(statement_type)
        if existing is None:
            current[statement_type] = record
            continue
        existing_time = existing.data_available_time or existing.event_time
        candidate_time = record.data_available_time or record.event_time
        if candidate_time > existing_time:
            current[statement_type] = record
    return selected_period, current, records


def _comparable_record(
    *,
    all_records: list[PointInTimeFinancialRecord],
    current: PointInTimeFinancialRecord | None,
    statement_type: str,
) -> PointInTimeFinancialRecord | None:
    if current is None or current.report_period is None:
        return None
    current_period = _period_key(current.report_period)
    if len(current_period) != 8:
        return None
    prior_period = f"{int(current_period[:4]) - 1}{current_period[4:]}"
    candidates = [
        record
        for record in all_records
        if (
            (record.statement_type or record.data_type.rsplit(":", 1)[-1])
            == statement_type
            and _period_key(record.report_period) == prior_period
            and record.period_type == current.period_type
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda record: record.data_available_time or record.event_time,
    )


def calculate_fundamental_metrics(
    *,
    records: list[PointInTimeFinancialRecord],
    valuation_point: MarketValuationPoint | None,
) -> tuple[
    FundamentalMetrics,
    list[str],
    list[FundamentalRiskFlag],
    list[PointInTimeFinancialRecord],
]:
    risk_flags: set[FundamentalRiskFlag] = set()
    report_period, current, all_records = _latest_statement_set(records)
    balance = current.get("balancesheet")
    income = current.get("income")
    cashflow = current.get("cashflow")
    indicator = current.get("fina_indicator")

    previous_balance = _comparable_record(
        all_records=all_records,
        current=balance,
        statement_type="balancesheet",
    )
    previous_income = _comparable_record(
        all_records=all_records,
        current=income,
        statement_type="income",
    )

    balance_payload = balance.payload if balance is not None else {}
    income_payload = income.payload if income is not None else {}
    cashflow_payload = cashflow.payload if cashflow is not None else {}

    debt_ratio = _ratio(
        balance_payload.get("total_liab"),
        balance_payload.get("total_assets"),
        risk_flags,
    )
    operating_cash_flow = _number(
        cashflow_payload.get("n_cashflow_act")
    )

    roe_self_calculated: float | None = None
    if balance is not None and previous_balance is not None:
        current_equity = _number(
            balance_payload.get("total_hldr_eqy_exc_min_int")
        )
        previous_equity = _number(
            previous_balance.payload.get(
                "total_hldr_eqy_exc_min_int"
            )
        )
        net_income = _number(income_payload.get("n_income_attr_p"))
        if (
            current_equity is not None
            and previous_equity is not None
            and net_income is not None
        ):
            roe_self_calculated = _ratio(
                net_income,
                (current_equity + previous_equity) / 2,
                risk_flags,
            )
    indicator_payload = indicator.payload if indicator is not None else {}
    official_roe = _number(indicator_payload.get("roe"))
    official_roe_dt = _number(indicator_payload.get("roe_dt"))
    roe = official_roe / 100 if official_roe is not None else roe_self_calculated
    roe_dt = official_roe_dt / 100 if official_roe_dt is not None else None
    roe_source = (
        "Tushare官方加权口径"
        if official_roe is not None
        else "自算（官方缺失）"
        if roe_self_calculated is not None
        else None
    )
    if roe is None:
        risk_flags.add(FundamentalRiskFlag.INSUFFICIENT_HISTORY)

    revenue_growth: float | None = None
    net_profit_growth: float | None = None
    if income is not None and previous_income is not None:
        revenue_growth_ratio = _ratio(
            income_payload.get("revenue")
            or income_payload.get("total_revenue"),
            previous_income.payload.get("revenue")
            or previous_income.payload.get("total_revenue"),
            risk_flags,
        )
        if revenue_growth_ratio is not None:
            revenue_growth = revenue_growth_ratio - 1
        profit_growth_ratio = _ratio(
            income_payload.get("n_income_attr_p"),
            previous_income.payload.get("n_income_attr_p"),
            risk_flags,
        )
        if profit_growth_ratio is not None:
            net_profit_growth = profit_growth_ratio - 1
    official_revenue_growth = _number(indicator_payload.get("or_yoy"))
    official_net_profit_growth = _number(indicator_payload.get("netprofit_yoy"))
    if official_revenue_growth is not None:
        revenue_growth = official_revenue_growth / 100
    if official_net_profit_growth is not None:
        net_profit_growth = official_net_profit_growth / 100
    if revenue_growth is None or net_profit_growth is None:
        risk_flags.add(FundamentalRiskFlag.INSUFFICIENT_HISTORY)

    pe_ttm: float | None = None
    pb: float | None = None
    shares = _number(balance_payload.get("total_share"))
    equity = _number(
        balance_payload.get("total_hldr_eqy_exc_min_int")
    )
    if valuation_point is not None and shares is not None:
        market_value = valuation_point.close * shares
        pb = _ratio(market_value, equity, risk_flags)
        if income is not None and income.period_type == "ANNUAL":
            pe_ttm = _ratio(
                market_value,
                income_payload.get("n_income_attr_p"),
                risk_flags,
            )
    else:
        risk_flags.add(FundamentalRiskFlag.VALUATION_DATA_MISSING)

    peg: float | None = None
    if (
        pe_ttm is not None
        and pe_ttm > 0
        and net_profit_growth is not None
        and net_profit_growth > 0
    ):
        peg = pe_ttm / (net_profit_growth * 100)
    elif pe_ttm is not None or net_profit_growth is not None:
        risk_flags.add(FundamentalRiskFlag.INVALID_DENOMINATOR)

    net_income_attr = _number(income_payload.get("n_income_attr_p"))
    invest_income = _number(income_payload.get("invest_income"))
    net_profit_deducted = _number(income_payload.get("dt_netprofit"))
    if (
        net_profit_deducted is None
        and net_income_attr is not None
        and invest_income is not None
    ):
        # 扣非字段缺失时用归母-投资收益近似（盈新发展案例：
        # 归母8610万全由投资收益3.88亿撑起）
        net_profit_deducted = net_income_attr - invest_income
    invest_income_ratio: float | None = None
    if (
        invest_income is not None
        and net_income_attr is not None
        and net_income_attr > 0
    ):
        invest_income_ratio = invest_income / net_income_attr

    metrics = FundamentalMetrics(
        pe_ttm=pe_ttm,
        pb=pb,
        roe=roe,
        roe_dt=roe_dt,
        roe_source=roe_source,
        revenue_growth=revenue_growth,
        net_profit_growth=net_profit_growth,
        debt_ratio=debt_ratio,
        operating_cash_flow=operating_cash_flow,
        operating_cash_flow_positive=(
            None
            if operating_cash_flow is None
            else operating_cash_flow > 0
        ),
        peg=peg,
        net_profit_deducted=net_profit_deducted,
        invest_income=invest_income,
        invest_income_ratio=invest_income_ratio,
        report_period=report_period,
        statement_types=sorted(current),
        accounting_scope=(
            balance.accounting_scope if balance is not None else None
        ),
        period_type=(
            balance.period_type
            if balance is not None
            else income.period_type if income is not None else None
        ),
    )
    missing_fields = [
        field
        for field in CORE_METRIC_FIELDS
        if getattr(metrics, field) is None
    ]
    if missing_fields:
        risk_flags.add(FundamentalRiskFlag.DATA_GAP)
    return (
        metrics,
        missing_fields,
        sorted(risk_flags, key=lambda flag: flag.value),
        list(current.values()),
    )


def report_period_as_of(report_period: str | None, fallback: datetime) -> datetime:
    if report_period:
        normalized = report_period.replace("-", "")
        if len(normalized) == 8 and normalized.isdigit():
            try:
                return datetime.strptime(normalized, "%Y%m%d").replace(
                    tzinfo=fallback.tzinfo
                )
            except ValueError:
                pass
    return fallback


__all__ = [
    "CORE_METRIC_FIELDS",
    "FUNDAMENTAL_METRICS_VERSION",
    "calculate_fundamental_metrics",
    "report_period_as_of",
]
