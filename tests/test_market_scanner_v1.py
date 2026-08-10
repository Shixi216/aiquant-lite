from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from config.settings import settings
from database.migrations.v0110_market_scanner_v1 import (
    MIGRATION_ID,
    _checksum,
    apply_migration,
)
from trading.research.orchestration.models import FORMAL_WEIGHTS
from trading.scanner.anomaly_detector import ScannerAnomalyDetector
from trading.scanner.feature_loader import ScannerFeatureSet
from trading.scanner.models import (
    AnomalyType,
    MissingDataPolicy,
    ScannerRiskFlag,
)
from trading.scanner.query_ast import (
    BooleanNode,
    BooleanOperator,
    ComparisonNode,
    ComparisonOperator,
    MembershipNode,
    MembershipOperator,
    RangeNode,
    ScannerField,
)
from trading.scanner.query_parser import LocalChineseQueryParser
from trading.scanner.query_validator import validate_filter
from trading.scanner.ranking import ScannerRanker
from trading.scanner.repository import ScannerRepository
from trading.scanner.result_cards import build_candidate_cards
from trading.scanner.routes import _error
from trading.scanner.schemas import (
    ScannerParseRequest,
    ScannerQueryPlan,
    ScannerScanRequest,
    ScannerScanResponse,
)
from trading.scanner.service import MarketScannerService
from trading.scanner.universe_filter import UniverseFilter
from trading.schemas import AnalysisMode


CUTOFF = datetime(2026, 7, 30, 3, 0, tzinfo=timezone(timedelta(hours=8)))


def _parse(query: str, **kwargs: Any):
    return LocalChineseQueryParser().parse(
        ScannerParseRequest(
            query=query,
            data_cutoff=CUTOFF,
            **kwargs,
        )
    )


def _nodes(plan: ScannerQueryPlan) -> list[Any]:
    output: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, BooleanNode):
            for child in node.children:
                walk(child)
        else:
            output.append(node)

    for item in plan.filters:
        walk(item)
    return output


def test_scanner_rejects_empty_universe_instead_of_false_success() -> None:
    service = MarketScannerService(repository=object())  # type: ignore[arg-type]

    class EmptyLoader:
        def load(self, data_cutoff: datetime) -> ScannerFeatureSet:
            del data_cutoff
            return ScannerFeatureSet(
                frame=pd.DataFrame(),
                input_snapshot_hash="empty",
                snapshot_id=None,
                snapshot_time=None,
                cold_cache=True,
                data_read_ms=0,
                feature_compute_ms=0,
                database_session_count=1,
                database_query_count=1,
            )

    service.loader = EmptyLoader()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="SCANNER_EMPTY_UNIVERSE"):
        service.scan(
            ScannerScanRequest(
                query="全A股前20只",
                data_cutoff=datetime.now().astimezone(),
                top_n=20,
            )
        )


def test_scanner_empty_universe_maps_to_explicit_conflict() -> None:
    response = _error(RuntimeError("SCANNER_EMPTY_UNIVERSE"))

    assert response.status_code == 409
    assert response.detail == "SCANNER_EMPTY_UNIVERSE"


@pytest.mark.parametrize(
    ("query", "field", "expected"),
    [
        ("价格10到20元", ScannerField.CURRENT_PRICE, (10.0, 20.0)),
        ("成交额大于5亿", ScannerField.AMOUNT, 500_000_000.0),
        ("换手率3%到10%", ScannerField.TURNOVER_RATE, (0.03, 0.10)),
        ("量比大于1.5", ScannerField.VOLUME_RATIO_5D, 1.5),
        ("成交量大于2万手", ScannerField.CURRENT_VOLUME, 2_000_000.0),
        ("技术面为正", ScannerField.TECHNICAL_SCORE, 0.0),
        ("资金面为正", ScannerField.CAPITAL_FLOW_SCORE, 0.0),
        ("至少2个因子可用", ScannerField.FACTOR_COVERAGE_COUNT, 2),
    ],
)
def test_parser_numeric_and_factor_conditions(
    query: str,
    field: ScannerField,
    expected: Any,
) -> None:
    response = _parse(query)
    assert response.clarification_required is False
    node = next(item for item in _nodes(response.parsed_query) if item.field == field)
    actual = (
        (node.minimum, node.maximum)
        if isinstance(node, RangeNode)
        else node.value
    )
    assert actual == expected


@pytest.mark.parametrize(
    ("query", "board"),
    [
        ("沪市主板前20只", "SH_MAIN"),
        ("深市主板前20只", "SZ_MAIN"),
        ("创业板前20只", "CHINEXT"),
        ("科创板前20只", "STAR"),
        ("北交所前20只", "BEIJING"),
    ],
)
def test_parser_board_aliases(query: str, board: str) -> None:
    assert _parse(query).parsed_query.include_boards == [board]


def test_parser_supports_natural_price_trend_and_concise_industry() -> None:
    price_trend = _parse("10元左右、有上涨趋势").parsed_query
    industry = _parse("只看半导体").parsed_query

    assert price_trend is not None
    assert industry is not None
    assert any(
        isinstance(node, RangeNode)
        and node.field == ScannerField.CURRENT_PRICE
        and node.minimum == pytest.approx(9.0)
        and node.maximum == pytest.approx(11.0)
        for node in _nodes(price_trend)
    )
    assert any(
        isinstance(node, ComparisonNode)
        and node.field == ScannerField.MA_BULLISH
        and node.value is True
        for node in _nodes(price_trend)
    )
    assert industry.include_industries == ["半导体"]


def test_zero_industry_matches_have_a_truthful_reason() -> None:
    service = MarketScannerService()
    service.loader = _FakeLoader(_frame())  # type: ignore[assignment]

    result = service.scan(
        ScannerScanRequest(
            query="只看半导体",
            data_cutoff=datetime.now().astimezone(),
            top_n=20,
        )
    )

    assert isinstance(result, ScannerScanResponse)
    assert result.matched_count == 0
    assert result.no_match_reason is not None
    assert "行业分类" in result.no_match_reason


def test_parser_excludes_st_and_suspension() -> None:
    plan = _parse("排除ST和停牌").parsed_query
    values = {(item.field, item.value) for item in _nodes(plan)}
    assert (ScannerField.IS_ST, False) in values
    assert (ScannerField.IS_SUSPENDED, False) in values


def test_parser_excludes_new_listings() -> None:
    plan = _parse("排除上市不足60日的新股").parsed_query
    node = _nodes(plan)[0]
    assert node.field == ScannerField.LISTING_AGE_DAYS
    assert node.value == 60


def test_parser_and_or_precedence() -> None:
    plan = _parse(
        "价格大于10元或成交额超过5亿且量比大于1.5"
    ).parsed_query
    root = plan.filters[0]
    assert isinstance(root, BooleanNode)
    assert root.operator == BooleanOperator.OR
    assert isinstance(root.children[1], BooleanNode)
    assert root.children[1].operator == BooleanOperator.AND


@pytest.mark.parametrize(
    "query",
    [
        "成交额大于5",
        "价格大于10",
        "换手率大于3",
    ],
)
def test_parser_ambiguous_unit_requires_clarification(query: str) -> None:
    response = _parse(query)
    assert response.clarification_required is True
    assert ScannerRiskFlag.QUERY_AMBIGUOUS in response.risk_flags


@pytest.mark.parametrize("query", ["市盈率低于20", "目标价20元", "建议买入"])
def test_parser_rejects_unsupported_or_trade_fields(query: str) -> None:
    response = _parse(query)
    assert response.clarification_required is True
    assert ScannerRiskFlag.QUERY_FIELD_UNSUPPORTED in response.risk_flags


@pytest.mark.parametrize(
    "query",
    [
        "select * from stock_universe",
        "价格大于10元;drop table x",
        "__import__('os')",
        "eval(1+1)",
    ],
)
def test_parser_rejects_sql_and_python_injection(query: str) -> None:
    response = _parse(query)
    assert response.parsed_query is None
    assert response.clarification_required is True


def test_same_query_generates_stable_plan_hash() -> None:
    left = _parse("价格10到20元，成交额超过5亿").parsed_query
    right = _parse("价格10到20元，成交额超过5亿").parsed_query
    assert left.plan_hash == right.plan_hash
    assert left.query_id == right.query_id


def test_local_parser_needs_no_model() -> None:
    response = _parse("全A股前20只")
    assert response.parser_model_call_count == 0
    assert response.parsed_query.parser_type.value == "LOCAL"


def test_model_parser_is_called_at_most_once() -> None:
    calls = 0

    def model(_: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"filters": [], "top_n": 20}

    response = LocalChineseQueryParser(model).parse(
        ScannerParseRequest(
            query="成交额大于5",
            data_cutoff=CUTOFF,
            allow_parser_model=True,
        )
    )
    assert calls == 1
    assert response.parser_model_call_count == 1
    assert response.clarification_required is False


def test_model_parser_trade_output_is_rejected() -> None:
    response = LocalChineseQueryParser(
        lambda _: {"filters": [], "action": "BUY"}
    ).parse(
        ScannerParseRequest(
            query="成交额大于5",
            data_cutoff=CUTOFF,
            allow_parser_model=True,
        )
    )
    assert response.clarification_required is True
    assert ScannerRiskFlag.MODEL_PARSER_FAILED in response.risk_flags


def _frame() -> pd.DataFrame:
    snapshot = pd.Timestamp("2026-07-29T12:58:53+08:00")
    rows = []
    for index, symbol in enumerate(("000001.SZ", "300001.SZ", "920001.BJ")):
        rows.append(
            {
                "symbol": symbol,
                "board": ("SZ_MAIN", "CHINEXT", "BEIJING")[index],
                "short_name": f"样本{index}",
                "industry": "I65软件和信息技术服务业",
                "is_st": index == 2,
                "is_suspended": index == 1,
                "listing_age_days": 100 + index,
                "current_price": (12.0, 20.0, None)[index],
                "current_volume": (2_000_000.0, 0.0, None)[index],
                "previous_close": (10.0, 21.0, None)[index],
                "current_open": (10.5, 20.5, None)[index],
                "current_high": (12.2, 21.0, None)[index],
                "current_low": (10.4, 19.5, None)[index],
                "amount": (600_000_000.0, 10_000_000.0, None)[index],
                "change_pct": (0.20, -0.05, None)[index],
                "turnover_rate": (0.12, 0.02, None)[index],
                "amplitude": (0.18, 0.07, None)[index],
                "volume_ratio_5d": (2.5, 0.4, None)[index],
                "volume_ratio_20d": (2.0, 0.5, None)[index],
                "amount_ratio_5d": (2.2, 0.4, None)[index],
                "amount_ratio_20d": (2.0, 0.4, None)[index],
                "amount_market_percentile": (0.9, 0.2, 0.0)[index],
                "sma5": 11.0,
                "sma10": 10.5,
                "sma20": 10.0,
                "sma60": 9.0,
                "above_sma5": index == 0,
                "above_sma10": index == 0,
                "above_sma20": index == 0,
                "above_sma60": index == 0,
                "ma_bullish": index == 0,
                "ma_bearish": False,
                "rsi14": (70.0, 30.0, None)[index],
                "macd_state": ("POSITIVE", "NEGATIVE", None)[index],
                "breakout_high_20d": index == 0,
                "breakdown_low_20d": index == 1,
                "consecutive_up_days": (3, 0, 0)[index],
                "consecutive_down_days": (0, 2, 0)[index],
                "volatility_20d": (0.7, 0.2, None)[index],
                "gap_pct": (0.05, -0.04, None)[index],
                "price_volume_state": (
                    "PRICE_UP_VOLUME_UP",
                    "PRICE_DOWN_VOLUME_DOWN",
                    "UNKNOWN",
                )[index],
                "technical_score": (0.5, -0.2, None)[index],
                "capital_flow_score": (0.6, -0.3, 0.0)[index],
                "shadow_composite_score": (0.55, -0.25, 0.0)[index],
                "composite_confidence": (0.5, 0.2, 0.1)[index],
                "factor_coverage_count": (2, 2, 1)[index],
                "factor_coverage_ratio": (0.4, 0.4, 0.2)[index],
                "available_factor_names": (
                    ["CAPITAL_FLOW", "TECHNICAL"],
                    ["CAPITAL_FLOW", "TECHNICAL"],
                    ["CAPITAL_FLOW"],
                )[index],
                "fundamental_score": None,
                "sentiment_score": None,
                "policy_news_score": None,
                "snapshot_stale": True,
                "snapshot_time": snapshot,
                "snapshot_id": "mkt_test",
                "current_valid": index == 0,
                "price_limit_type": (
                    "MAIN_10_PERCENT",
                    "GROWTH_20_PERCENT",
                    "BEIJING_30_PERCENT",
                )[index],
                "history_bar_ids": [f"hbar_{index}_{j}" for j in range(5)],
                "history_count": 60 if index < 2 else 1,
                "risk_flags": [],
            }
        )
    return pd.DataFrame(rows)


def _plan(filters: list[Any] | None = None, **updates: Any) -> ScannerQueryPlan:
    return _parse("全A股前20只").parsed_query.model_copy(
        update={"filters": filters or [], **updates}
    )


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (ComparisonOperator.GT, ["300001.SZ"]),
        (ComparisonOperator.GTE, ["300001.SZ"]),
        (ComparisonOperator.LT, ["000001.SZ"]),
        (ComparisonOperator.LTE, ["000001.SZ"]),
    ],
)
def test_hard_comparison_filters(operator: ComparisonOperator, expected: list[str]) -> None:
    node = ComparisonNode(
        field=ScannerField.CURRENT_PRICE,
        operator=operator,
        value=15.0,
    )
    result = UniverseFilter.apply(_frame(), _plan([node]))
    assert result.frame["symbol"].tolist() == expected


@pytest.mark.parametrize(
    ("policy", "expected_count"),
    [
        (MissingDataPolicy.EXCLUDE, 2),
        (MissingDataPolicy.INCLUDE_WITH_FLAG, 3),
        (MissingDataPolicy.IGNORE_FILTER, 3),
    ],
)
def test_missing_data_policies(
    policy: MissingDataPolicy,
    expected_count: int,
) -> None:
    node = ComparisonNode(
        field=ScannerField.CURRENT_PRICE,
        operator=ComparisonOperator.GT,
        value=0.0,
    )
    result = UniverseFilter.apply(
        _frame(),
        _plan([node], missing_data_policy=policy),
    )
    assert len(result.frame) == expected_count


def test_missing_data_require_clarification() -> None:
    node = ComparisonNode(
        field=ScannerField.CURRENT_PRICE,
        operator=ComparisonOperator.GT,
        value=0.0,
    )
    with pytest.raises(ValueError, match="requires clarification"):
        UniverseFilter.apply(
            _frame(),
            _plan(
                [node],
                missing_data_policy=MissingDataPolicy.REQUIRE_CLARIFICATION,
            ),
        )


def test_layer_partition_keeps_formal_core_unchanged() -> None:
    node = ComparisonNode(
        field=ScannerField.CURRENT_PRICE,
        operator=ComparisonOperator.GT,
        value=13.0,
    )
    plan = _plan([node])
    formal = UniverseFilter.apply(_frame(), plan)
    layers = UniverseFilter.partition(_frame(), plan, core_result=formal)
    assert layers.core["symbol"].tolist() == formal.frame["symbol"].tolist()
    assert layers.near["symbol"].tolist() == ["000001.SZ"]
    assert layers.control["symbol"].tolist() == ["920001.BJ"]


def test_membership_filter() -> None:
    node = MembershipNode(
        field=ScannerField.BOARD,
        operator=MembershipOperator.IN,
        values=["BEIJING"],
    )
    assert UniverseFilter.apply(_frame(), _plan([node])).frame.iloc[0][
        "symbol"
    ].endswith(".BJ")


@pytest.mark.parametrize(
    "expected",
    [
        AnomalyType.PRICE_SURGE,
        AnomalyType.VOLUME_SPIKE,
        AnomalyType.AMOUNT_SPIKE,
        AnomalyType.HIGH_TURNOVER,
        AnomalyType.PRICE_UP_VOLUME_UP,
        AnomalyType.BREAKOUT_HIGH,
        AnomalyType.VOLATILITY_SPIKE,
        AnomalyType.GAP_UP,
        AnomalyType.MULTI_SIGNAL_CONFLUENCE,
    ],
)
def test_anomaly_detector_positive_sample(expected: AnomalyType) -> None:
    output = ScannerAnomalyDetector().detect(_frame())
    assert expected in output.iloc[0]["anomaly_types"]


def test_suspended_stock_has_no_price_anomaly() -> None:
    output = ScannerAnomalyDetector().detect(_frame())
    assert output.iloc[1]["anomaly_types"] == []
    assert ScannerRiskFlag.POSSIBLE_SUSPENSION in output.iloc[1][
        "local_risk_flags"
    ]


def test_unknown_price_limit_rule_is_flagged() -> None:
    frame = _frame().iloc[[0]].copy()
    frame["price_limit_type"] = "UNKNOWN"
    output = ScannerAnomalyDetector().detect(frame)
    assert ScannerRiskFlag.PRICE_LIMIT_RULE_UNKNOWN in output.iloc[0][
        "local_risk_flags"
    ]


def test_rank_is_stable_and_tiebreaks_by_symbol() -> None:
    frame = ScannerAnomalyDetector().detect(_frame().iloc[[0, 0]].copy())
    frame.loc[:, "symbol"] = ["000002.SZ", "000001.SZ"]
    frame["missing_filter_fields_internal"] = [[], []]
    ranked = ScannerRanker().rank(frame, _plan())
    assert ranked["symbol"].tolist() == ["000001.SZ", "000002.SZ"]


def test_shadow_is_not_the_only_ranking_component() -> None:
    weights = ScannerRanker().weights
    assert weights.shadow_composite < weights.technical + weights.capital_flow
    assert weights.query_match > weights.shadow_composite


def test_low_coverage_reduces_ranking_score() -> None:
    frame = _frame().iloc[[0, 0]].copy()
    frame.loc[:, "factor_coverage_ratio"] = [0.4, 0.2]
    frame.loc[:, "factor_coverage_count"] = [2, 1]
    frame.loc[:, "symbol"] = ["000001.SZ", "000002.SZ"]
    detected = ScannerAnomalyDetector().detect(frame)
    detected["missing_filter_fields_internal"] = [[], []]
    ranked = ScannerRanker().rank(detected, _plan())
    scores = dict(zip(ranked["symbol"], ranked["scanner_score"], strict=True))
    assert scores["000001.SZ"] > scores["000002.SZ"]


def _ranked_frame() -> pd.DataFrame:
    frame = ScannerAnomalyDetector().detect(_frame())
    frame["missing_filter_fields_internal"] = [[], [], []]
    return ScannerRanker().rank(frame, _plan())


def test_candidate_card_shows_factor_coverage() -> None:
    card = build_candidate_cards(_ranked_frame(), limit=10)[0]
    assert card.factor_coverage == "2/5"


def test_candidate_card_shows_missing_factors() -> None:
    card = build_candidate_cards(_ranked_frame(), limit=10)[0]
    assert len(card.missing_factors) == 3


def test_candidate_card_is_never_trade_recommendation() -> None:
    card = build_candidate_cards(_ranked_frame(), limit=10)[0]
    assert card.is_trade_recommendation is False
    assert "建议买入" not in card.model_dump_json()


def test_detailed_evidence_is_only_candidate_summary() -> None:
    card = build_candidate_cards(_ranked_frame(), limit=10)[0]
    assert card.evidence_summary["no_full_market_evidence_graph"] is True
    assert len(card.evidence_summary["evidence_ids"]) <= 5


class _FakeRepository:
    def __init__(self) -> None:
        self.saved: set[str] = set()

    def save_scan(self, response: ScannerScanResponse) -> bool:
        new = response.run_id not in self.saved
        self.saved.add(response.run_id)
        return new


class _FakeLoader:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def load(self, _: datetime) -> ScannerFeatureSet:
        return ScannerFeatureSet(
            frame=self.frame,
            input_snapshot_hash="a" * 64,
            snapshot_id="mkt_test",
            snapshot_time=pd.Timestamp(
                "2026-07-29T12:58:53+08:00"
            ).to_pydatetime(),
            cold_cache=True,
            data_read_ms=1.0,
            feature_compute_ms=1.0,
            database_session_count=1,
            database_query_count=1,
        )


def _service() -> MarketScannerService:
    service = MarketScannerService(_FakeRepository())
    service.loader = _FakeLoader(_frame())
    return service


def test_screening_has_zero_network_model_and_decision_calls() -> None:
    result = _service().scan(
        ScannerScanRequest(
            query="全A股前20只",
            data_cutoff=CUTOFF,
        )
    )
    assert isinstance(result, ScannerScanResponse)
    assert result.performance.network_request_count == 0
    assert result.performance.model_call_count == 0
    assert result.performance.decision_packet_count == 0
    assert result.decision_called is False


def test_scan_response_exposes_three_non_overlapping_layers() -> None:
    node = ComparisonNode(
        field=ScannerField.CURRENT_PRICE,
        operator=ComparisonOperator.GT,
        value=13.0,
    )
    result = _service().scan(
        ScannerScanRequest(plan=_plan([node]), data_cutoff=CUTOFF)
    )
    assert isinstance(result, ScannerScanResponse)
    assert result.candidates == result.candidate_layers.core
    assert [card.symbol for card in result.candidate_layers.near] == [
        "000001.SZ"
    ]
    assert [card.symbol for card in result.candidate_layers.control] == [
        "920001.BJ"
    ]
    assert result.candidate_layers.near[0].candidate_layer == "NEAR"


def test_research_handoff_caps_ai_at_ten() -> None:
    service = _service()
    result = service.scan(
        ScannerScanRequest(query="全A股前20只", data_cutoff=CUTOFF)
    )
    handoff = service.research_handoff(result)
    assert len(handoff["symbols"]) <= 30
    assert len(handoff["ai_deep_analysis_symbols"]) <= 10
    assert handoff["decision_called"] is False


def test_decision_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="explicit"):
        _service().scan(
            ScannerScanRequest(
                query="全A股前20只",
                analysis_mode=AnalysisMode.DECISION,
                data_cutoff=CUTOFF,
            )
        )


def test_scan_persistence_identity_is_idempotent() -> None:
    service = _service()
    request = ScannerScanRequest(
        query="全A股前20只",
        data_cutoff=CUTOFF,
        persist_run=True,
    )
    first = service.scan(request)
    second = service.scan(request)
    assert first.run_id == second.run_id
    assert len(service.repository.saved) == 1


def test_0110_migration_is_idempotent(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "scanner.duckdb")) as connection:
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False


def test_0110_creates_six_scanner_tables(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "scanner.duckdb")) as connection:
        apply_migration(connection)
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_name LIKE 'scanner_%'
                """
            ).fetchall()
        }
    assert len(tables) == 6


def test_0110_checksum_is_stable() -> None:
    assert _checksum() == _checksum()
    assert len(_checksum()) == 64


def test_0110_does_not_delete_existing_data(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "scanner.duckdb")) as connection:
        connection.execute("CREATE TABLE sentinel (value INTEGER)")
        connection.execute("INSERT INTO sentinel VALUES (1)")
        apply_migration(connection)
        assert connection.execute("SELECT COUNT(*) FROM sentinel").fetchone()[0] == 1


def test_0110_records_its_migration_id(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "scanner.duckdb")) as connection:
        apply_migration(connection)
        row = connection.execute(
            "SELECT migration_id FROM schema_migrations"
        ).fetchone()
    assert row[0] == MIGRATION_ID


def test_formal_strategy_weights_remain_60_40() -> None:
    assert FORMAL_WEIGHTS == {
        next(key for key in FORMAL_WEIGHTS if key.value == "TECHNICAL"): 0.60,
        next(key for key in FORMAL_WEIGHTS if key.value == "FUNDAMENTAL"): 0.40,
    }


def test_scanner_source_has_no_live_broker_imports() -> None:
    root = Path("trading/scanner")
    payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
    ).casefold()
    assert "xtquant" not in payload
    assert "qmt" not in payload
    assert "broker" not in payload


def test_scanner_source_has_no_eval_or_exec_calls() -> None:
    payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("trading/scanner").glob("*.py")
    )
    assert "eval(" not in payload
    assert "exec(" not in payload


def test_query_validator_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="negative"):
        validate_filter(
            ComparisonNode(
                field=ScannerField.CURRENT_PRICE,
                operator=ComparisonOperator.GT,
                value=-1.0,
            )
        )


def test_future_data_cutoff_is_rejected() -> None:
    with pytest.raises(ValueError, match="future"):
        _service().scan(
            ScannerScanRequest(
                query="全A股前20只",
                data_cutoff=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )
        )


def test_top_n_schema_cap_is_enforced() -> None:
    with pytest.raises(ValueError):
        ScannerParseRequest(
            query="全A股",
            data_cutoff=CUTOFF,
            top_n=31,
        )


def test_real_repository_persistence_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "repository.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", database)
    repository = ScannerRepository()
    service = MarketScannerService(repository)
    service.loader = _FakeLoader(_frame())
    request = ScannerScanRequest(
        query="全A股前20只",
        data_cutoff=CUTOFF,
        persist_run=True,
    )
    first = service.scan(request)
    second = service.scan(request)
    assert first.run_id == second.run_id
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM scanner_runs").fetchone()[0] == 1
        assert (
            connection.execute("SELECT COUNT(*) FROM scanner_candidates").fetchone()[0]
            == first.returned_count
        )
