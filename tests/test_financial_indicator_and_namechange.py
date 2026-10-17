from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data_hub.schemas.unified import VerificationStatus
from data_hub.services.financial_statement_service import FinancialStatementService
from data_hub.services.name_change_service import (
    NameChangeService,
    render_name_change_history,
)
from trading.research.fundamental.metrics import calculate_fundamental_metrics
from trading.research.fundamental.models import PointInTimeFinancialRecord


TZ = ZoneInfo("Asia/Shanghai")


def _record(
    statement_type: str,
    period: str,
    payload: dict,
) -> PointInTimeFinancialRecord:
    event_time = datetime(2026, 4, 30, 18, tzinfo=TZ)
    return PointInTimeFinancialRecord(
        canonical_record_id=f"{statement_type}:{period}",
        symbol="002742.SZ",
        data_type=f"financial_statement:{statement_type}",
        report_period=period,
        announcement_time=event_time,
        data_available_time=event_time,
        event_time=event_time,
        primary_source="Tushare Pro",
        source_record_ids=[f"raw:{statement_type}:{period}"],
        verification_status=VerificationStatus.SINGLE_SOURCE,
        confidence=0.8,
        statement_type=statement_type,
        period_type="ANNUAL",
        payload=payload,
    )


def _financial_records(include_indicator: bool) -> list[PointInTimeFinancialRecord]:
    records = [
        _record("balancesheet", "20251231", {
            "total_assets": 2_000.0,
            "total_liab": 1_000.0,
            "total_hldr_eqy_exc_min_int": 1_000.0,
            "total_share": 100.0,
        }),
        _record("balancesheet", "20241231", {
            "total_assets": 2_100.0,
            "total_liab": 1_000.0,
            "total_hldr_eqy_exc_min_int": 1_100.0,
            "total_share": 100.0,
        }),
        _record("income", "20251231", {
            "n_income_attr_p": -96.6,
            "revenue": 500.0,
        }),
        _record("income", "20241231", {
            "n_income_attr_p": 20.0,
            "revenue": 1_000.0,
        }),
        _record("cashflow", "20251231", {"n_cashflow_act": -20.0}),
    ]
    if include_indicator:
        records.append(_record("fina_indicator", "20251231", {
            "roe": -12.72,
            "roe_dt": -12.53,
            "or_yoy": -50.51,
            "netprofit_yoy": -583.0,
        }))
    return records


def test_official_weighted_roe_has_priority_and_fallback_is_labeled() -> None:
    official, _, _, _ = calculate_fundamental_metrics(
        records=_financial_records(True), valuation_point=None,
    )
    assert official.roe == pytest.approx(-0.1272)
    assert official.roe_dt == pytest.approx(-0.1253)
    assert official.revenue_growth == pytest.approx(-0.5051)
    assert official.roe_source == "Tushare官方加权口径"

    fallback, _, _, _ = calculate_fundamental_metrics(
        records=_financial_records(False), valuation_point=None,
    )
    assert fallback.roe == pytest.approx(-96.6 / 1_050.0)
    assert fallback.roe_source == "自算（官方缺失）"


class _FinancialClient:
    def income(self, **kwargs):
        return pd.DataFrame([{"end_date": "20251231", "ann_date": "20260430", "n_income_attr_p": -96.6}])

    def balancesheet(self, **kwargs):
        return pd.DataFrame([{"end_date": "20251231", "ann_date": "20260430", "total_assets": 2_000.0}])

    def cashflow(self, **kwargs):
        return pd.DataFrame([{"end_date": "20251231", "ann_date": "20260430", "n_cashflow_act": -20.0}])

    def fina_indicator(self, **kwargs):
        assert "roe_dt" in kwargs["fields"]
        return pd.DataFrame([{"end_date": "20251231", "ann_date": "20260430", "roe": -12.72, "roe_dt": -12.53, "or_yoy": -50.51, "netprofit_yoy": -583.0}])


def test_financial_service_fetches_fina_indicator_without_changing_common_period() -> None:
    service = object.__new__(FinancialStatementService)
    service._client = _FinancialClient()
    response = service.get_financial_statement(
        "002742", "20250101", "20261231", persist=False,
    )
    indicator = next(
        record for record in response.records
        if record.data["statement_type"] == "fina_indicator"
    )
    assert response.report_period == "20251231"
    assert indicator.data["roe"] == -12.72


def test_namechange_cache_and_risk_render(monkeypatch, tmp_path) -> None:
    calls = 0

    class _NameClient:
        def namechange(self, **kwargs):
            nonlocal calls
            calls += 1
            return pd.DataFrame([
                {"name": "*ST三圣", "start_date": "20250430", "end_date": "20260531"},
                {"name": "三圣股份", "start_date": "20260601", "end_date": "20260611"},
                {"name": "冀衡医药", "start_date": "20260612", "end_date": ""},
            ])

    monkeypatch.setattr(
        "data_hub.services.capital_flow_enhanced.get_tushare_pro",
        lambda: _NameClient(),
    )
    service = NameChangeService(cache_dir=tmp_path)
    first = service.get_history("002742")
    second = service.get_history("002742")
    assert calls == 1
    assert first == second
    rendered = render_name_change_history(first, as_of=date(2026, 9, 2))
    assert rendered is not None
    assert "*ST三圣" in rendered and "冀衡医药" in rendered
    assert "曾戴帽/退市风险警示历史" in rendered
