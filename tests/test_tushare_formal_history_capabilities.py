from __future__ import annotations

import pandas as pd

from data_hub.providers.tushare_provider import TushareProvider


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def adj_factor(self, **kwargs):
        self.calls.append(("adj_factor", kwargs))
        return pd.DataFrame([
            {"ts_code": "600000.SH", "trade_date": "20250102", "adj_factor": 2.0}
        ])

    def suspend_d(self, **kwargs):
        self.calls.append(("suspend_d", kwargs))
        return pd.DataFrame([
            {"ts_code": "600000.SH", "suspend_date": "20250103", "resume_date": "20250106"}
        ])

    def namechange(self, **kwargs):
        self.calls.append(("namechange", kwargs))
        return pd.DataFrame([
            {"ts_code": "600000.SH", "name": "ST sample", "start_date": "20250101", "end_date": "20250201"}
        ])

    def index_daily(self, **kwargs):
        self.calls.append(("index_daily", kwargs))
        return pd.DataFrame([
            {"ts_code": "000300.SH", "trade_date": "20250102", "close": 4000.0}
        ])

    def index_classify(self, **kwargs):
        self.calls.append(("index_classify", kwargs))
        return pd.DataFrame([
            {"index_code": "801010.SI", "industry_name": "Agriculture", "level": "L1", "src": "SW2021"}
        ])
    def index_member_all(self, **kwargs):
        self.calls.append(("index_member_all", kwargs))
        return pd.DataFrame([
            {"ts_code": "600000.SH", "l1_code": "801010.SI", "in_date": "20200101"}
        ])


def provider() -> TushareProvider:
    value = object.__new__(TushareProvider)
    value._client = FakeClient()
    return value


def test_formal_history_capabilities_use_explicit_dates() -> None:
    value = provider()
    assert value.get_adjustment_factors("600000.SH", "2025-01-01", "2025-01-31")[0]["adj_factor"] == 2.0
    assert value.get_suspension_history("20250101", "20250131")[0]["suspend_date"] == "20250103"
    assert value.get_name_change_history("20250101", "20250131")[0]["name"] == "ST sample"
    assert value.get_index_daily("000300.SH", "20250101", "20250131")[0]["close"] == 4000.0
    assert value.get_index_members("801010.SI")[0]["ts_code"] == "600000.SH"
    assert value.get_sw_industry_indices()[0]["index_code"] == "801010.SI"


def test_formal_history_capabilities_reject_missing_pit_fields() -> None:
    value = provider()
    value._client.adj_factor = lambda **_: pd.DataFrame([{"ts_code": "600000.SH"}])
    try:
        value.get_adjustment_factors("600000.SH", "20250101", "20250131")
    except RuntimeError as exc:
        assert "trade_date" in str(exc)
    else:
        raise AssertionError("missing PIT field was accepted")