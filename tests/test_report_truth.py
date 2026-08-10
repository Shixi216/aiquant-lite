from __future__ import annotations

import pytest

from trading.decision_support.action import Action
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_response import DecisionResponse
from trading.decision_support.execution_status import compute_execution_status
from trading.review.report_truth import (
    CapitalFlowStatement,
    FormalReportBlockedError,
    FormalReportDraft,
    ReportIndicator,
    ReportTruthValidator,
    RiskEventStatement,
    TargetPriceStatement,
)


def _response(*, data_status: DataStatus = DataStatus.FRESH) -> DecisionResponse:
    return DecisionResponse(
        action=Action.BUY,
        data_status=data_status,
        frozen_entry_zone=[9.0, 11.0],
        frozen_preferred_zone=[9.0, 10.0],
        frozen_stop_loss_price=8.0,
    )


def _draft(**updates: object) -> FormalReportDraft:
    response = _response()
    status, _ = compute_execution_status(
        current_price=9.5,
        action="BUY",
        veto_triggered=False,
        frozen_entry_zone=response.frozen_entry_zone,
        frozen_preferred_zone=response.frozen_preferred_zone,
        frozen_stop_loss_price=response.frozen_stop_loss_price,
    )
    values: dict[str, object] = {
        "action": "BUY",
        "execution_status": status,
        "current_price": 9.5,
        "indicators": (
            ReportIndicator("RSI14", 55.0, "bar_rsi"),
            ReportIndicator("ATR14", 0.5, "bar_atr"),
            ReportIndicator("SMA20", 9.2, "bar_ma"),
        ),
    }
    values.update(updates)
    return FormalReportDraft(**values)  # type: ignore[arg-type]


def _codes(response: DecisionResponse, draft: FormalReportDraft) -> set[str]:
    return {
        issue.code for issue in ReportTruthValidator().validate(response, draft).issues
    }


def test_clean_formal_report_passes_and_renders() -> None:
    validator = ReportTruthValidator()
    draft = _draft()
    assert validator.validate(_response(), draft).passed is True
    assert validator.render_validated(_response(), draft, lambda _: "ok") == "ok"


def test_action_must_equal_decision_engine_result() -> None:
    assert "ACTION_MISMATCH" in _codes(_response(), _draft(action="WAIT"))


def test_execution_status_must_use_frozen_zones() -> None:
    assert "EXECUTION_STATUS_NOT_FROZEN" in _codes(
        _response(), _draft(execution_status="等待确认")
    )


@pytest.mark.parametrize(
    "indicator",
    [
        ReportIndicator("RSI14", 55.0, "bar", estimated=True),
        ReportIndicator("ATR14", None, "bar"),
        ReportIndicator("SMA20", 9.2, None),
    ],
)
def test_rsi_atr_and_ma_must_be_measured(indicator: ReportIndicator) -> None:
    assert "INDICATOR_NOT_MEASURED" in _codes(
        _response(), _draft(indicators=(indicator,))
    )


def test_no_risk_event_can_only_be_neutral() -> None:
    assert "NO_RISK_EVENT_NOT_NEUTRAL" in _codes(
        _response(),
        _draft(risk_events=(RiskEventStatement("NO_RISK_EVENT", "POSITIVE"),)),
    )


def test_target_below_current_cannot_be_positive() -> None:
    assert "LOW_TARGET_MARKED_POSITIVE" in _codes(
        _response(),
        _draft(target_prices=(TargetPriceStatement(9.0, "BULLISH"),)),
    )


def test_margin_financing_cannot_be_main_force() -> None:
    assert "MARGIN_MISLABELED_MAIN_FORCE" in _codes(
        _response(),
        _draft(
            capital_flows=(
                CapitalFlowStatement("MARGIN_FINANCING", "MAIN_FORCE"),
            )
        ),
    )


@pytest.mark.parametrize("status", [DataStatus.STALE, DataStatus.FAILED])
def test_stale_or_failed_data_blocks_formal_report(status: DataStatus) -> None:
    validator = ReportTruthValidator()
    with pytest.raises(FormalReportBlockedError):
        validator.validate_or_raise(_response(data_status=status), _draft())


def test_severe_missing_data_blocks_formal_report() -> None:
    with pytest.raises(FormalReportBlockedError):
        ReportTruthValidator().validate_or_raise(
            _response(), _draft(severe_missing_fields=("daily_bars",))
        )
