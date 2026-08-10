from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from trading.scanner.models import (
    BOARD_ALIASES,
    LOCAL_PARSER_VERSION,
    MODEL_PARSER_VERSION,
    AnomalyType,
    ParserType,
    ScannerRiskFlag,
    SortDirection,
)
from trading.scanner.query_ast import (
    BooleanNode,
    BooleanOperator,
    ComparisonNode,
    ComparisonOperator,
    FilterNode,
    MembershipNode,
    MembershipOperator,
    RangeNode,
    ScannerField,
    SortNode,
)
from trading.scanner.query_validator import (
    ensure_safe_text,
    stable_plan_hash,
    validate_filter,
)
from trading.scanner.schemas import (
    ScannerParseRequest,
    ScannerParseResponse,
    ScannerQueryPlan,
)


ParserModel = Callable[[str], dict[str, Any] | str]
_NUMBER = r"(-?\d+(?:\.\d+)?)"
_SYMBOL = re.compile(r"(?<!\d)(\d{6})(?:\.(SH|SZ|BJ))?(?!\d)", re.I)
_TRADE_OUTPUT_KEYS = {
    "action",
    "buy",
    "sell",
    "hold",
    "order",
    "position",
    "target_price",
    "veto",
}


def _normalize(text: str) -> str:
    table = str.maketrans(
        {
            "，": ",",
            "。": ".",
            "％": "%",
            "：": ":",
            "；": ";",
            "（": "(",
            "）": ")",
            "　": " ",
        }
    )
    return re.sub(r"\s+", "", text.translate(table)).casefold()


def _amount(number: str, unit: str) -> float:
    value = float(number)
    multipliers = {
        "元": 1.0,
        "万元": 10_000.0,
        "万": 10_000.0,
        "亿元": 100_000_000.0,
        "亿": 100_000_000.0,
    }
    return value * multipliers[unit]


def _percent(number: str) -> float:
    return float(number) / 100.0


def _share_volume(number: str, magnitude: str, unit: str) -> float:
    multiplier = {"": 1.0, "万": 10_000.0, "亿": 100_000_000.0}[magnitude]
    lot = 100.0 if unit == "手" else 1.0
    return float(number) * multiplier * lot


def _comparison(
    field: ScannerField,
    operator: ComparisonOperator,
    value: bool | float | int | str,
) -> ComparisonNode:
    return ComparisonNode(field=field, operator=operator, value=value)


class LocalChineseQueryParser:
    """Deterministic Chinese parser. It never emits SQL or executable code."""

    def __init__(self, model_parser: ParserModel | None = None) -> None:
        self.model_parser = model_parser

    @staticmethod
    def _condition_nodes(text: str) -> list[FilterNode]:
        nodes: list[FilterNode] = []

        def ranges(
            pattern: str,
            field: ScannerField,
            converter: Callable[[str], float],
        ) -> None:
            for match in re.finditer(pattern, text):
                nodes.append(
                    RangeNode(
                        field=field,
                        minimum=converter(match.group(1)),
                        maximum=converter(match.group(2)),
                    )
                )

        ranges(
            rf"(?:价格|股价){_NUMBER}(?:到|至|-){_NUMBER}元",
            ScannerField.CURRENT_PRICE,
            float,
        )
        ranges(
            rf"(?:筛选|找出)?{_NUMBER}(?:到|至|-){_NUMBER}元",
            ScannerField.CURRENT_PRICE,
            float,
        )
        for match in re.finditer(rf"{_NUMBER}元左右", text):
            center = float(match.group(1))
            tolerance = max(0.5, center * 0.1)
            nodes.append(
                RangeNode(
                    field=ScannerField.CURRENT_PRICE,
                    minimum=max(0.01, center - tolerance),
                    maximum=center + tolerance,
                )
            )
        ranges(
            rf"换手率{_NUMBER}%(?:到|至|-){_NUMBER}%",
            ScannerField.TURNOVER_RATE,
            _percent,
        )
        ranges(
            rf"(?:涨跌幅|涨幅){_NUMBER}%(?:到|至|-){_NUMBER}%",
            ScannerField.CHANGE_PCT,
            _percent,
        )
        ranges(
            rf"振幅{_NUMBER}%(?:到|至|-){_NUMBER}%",
            ScannerField.AMPLITUDE,
            _percent,
        )
        ranges(
            rf"量比{_NUMBER}(?:到|至|-){_NUMBER}",
            ScannerField.VOLUME_RATIO_5D,
            float,
        )
        ranges(
            rf"rsi(?:14)?{_NUMBER}(?:到|至|-){_NUMBER}",
            ScannerField.RSI14,
            float,
        )
        ranges(
            rf"(?:技术分|技术面分数){_NUMBER}(?:到|至|-){_NUMBER}",
            ScannerField.TECHNICAL_SCORE,
            float,
        )
        ranges(
            rf"(?:资金分|资金面分数){_NUMBER}(?:到|至|-){_NUMBER}",
            ScannerField.CAPITAL_FLOW_SCORE,
            float,
        )
        ranges(
            rf"(?:影子综合分|shadow_composite){_NUMBER}(?:到|至|-){_NUMBER}",
            ScannerField.SHADOW_COMPOSITE_SCORE,
            float,
        )
        ranges(
            rf"波动率{_NUMBER}%(?:到|至|-){_NUMBER}%",
            ScannerField.VOLATILITY_20D,
            _percent,
        )

        amount_pattern = (
            rf"成交额(?:大于|超过|高于|不少于|至少){_NUMBER}"
            r"(亿元|万元|亿|万|元)"
        )
        for match in re.finditer(amount_pattern, text):
            nodes.append(
                _comparison(
                    ScannerField.AMOUNT,
                    ComparisonOperator.GT,
                    _amount(match.group(1), match.group(2)),
                )
            )
        for match in re.finditer(
            rf"成交额(?:小于|低于|不超过|至多){_NUMBER}(亿元|万元|亿|万|元)",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.AMOUNT,
                    ComparisonOperator.LTE,
                    _amount(match.group(1), match.group(2)),
                )
            )
        for match in re.finditer(
            rf"成交量(?:大于|超过|高于|不少于|至少){_NUMBER}(万|亿)?(股|手)",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.CURRENT_VOLUME,
                    ComparisonOperator.GTE,
                    _share_volume(
                        match.group(1),
                        match.group(2) or "",
                        match.group(3),
                    ),
                )
            )
        for match in re.finditer(
            rf"(?:价格|股价)(?:大于|超过|高于|不少于|至少){_NUMBER}元",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.CURRENT_PRICE,
                    ComparisonOperator.GTE,
                    float(match.group(1)),
                )
            )
        for match in re.finditer(
            rf"(?:价格|股价)(?:小于|低于|不超过|至多){_NUMBER}元",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.CURRENT_PRICE,
                    ComparisonOperator.LTE,
                    float(match.group(1)),
                )
            )
        for match in re.finditer(
            rf"量比(?:大于|超过|高于|不少于|至少){_NUMBER}",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.VOLUME_RATIO_5D,
                    ComparisonOperator.GTE,
                    float(match.group(1)),
                )
            )
        for match in re.finditer(
            rf"量比(?:小于|低于|不超过|至多){_NUMBER}",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.VOLUME_RATIO_5D,
                    ComparisonOperator.LTE,
                    float(match.group(1)),
                )
            )
        for match in re.finditer(
            rf"成交量(?:超过|大于)(5|20)日均量(?:的)?{_NUMBER}倍",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField(f"volume_ratio_{match.group(1)}d"),
                    ComparisonOperator.GTE,
                    float(match.group(2)),
                )
            )
        for match in re.finditer(
            rf"成交额(?:超过|大于)(5|20)日均额(?:的)?{_NUMBER}倍",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField(f"amount_ratio_{match.group(1)}d"),
                    ComparisonOperator.GTE,
                    float(match.group(2)),
                )
            )
        for match in re.finditer(
            rf"成交额市场百分位(?:大于|超过|高于){_NUMBER}%",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.AMOUNT_MARKET_PERCENTILE,
                    ComparisonOperator.GTE,
                    _percent(match.group(1)),
                )
            )
        for match in re.finditer(rf"涨幅{_NUMBER}%(?:以上|及以上)", text):
            nodes.append(
                _comparison(
                    ScannerField.CHANGE_PCT,
                    ComparisonOperator.GTE,
                    _percent(match.group(1)),
                )
            )
        for match in re.finditer(rf"跌幅不超过{_NUMBER}%", text):
            nodes.append(
                _comparison(
                    ScannerField.CHANGE_PCT,
                    ComparisonOperator.GTE,
                    -_percent(match.group(1)),
                )
            )
        for match in re.finditer(
            rf"换手率(?:大于|超过|高于|不少于){_NUMBER}%",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.TURNOVER_RATE,
                    ComparisonOperator.GTE,
                    _percent(match.group(1)),
                )
            )
        for match in re.finditer(
            rf"换手率(?:小于|低于|不超过|至多){_NUMBER}%",
            text,
        ):
            nodes.append(
                _comparison(
                    ScannerField.TURNOVER_RATE,
                    ComparisonOperator.LTE,
                    _percent(match.group(1)),
                )
            )
        for days in (5, 10, 20, 60):
            if re.search(rf"(?:站上|位于){days}日均线(?:之上|上方)?", text):
                nodes.append(
                    _comparison(
                        ScannerField(f"above_sma{days}"),
                        ComparisonOperator.EQ,
                        True,
                    )
                )
            if re.search(rf"(?:跌破|位于){days}日均线(?:之下|下方)", text):
                nodes.append(
                    _comparison(
                        ScannerField(f"above_sma{days}"),
                        ComparisonOperator.EQ,
                        False,
                    )
                )
        if "均线多头" in text or "多头排列" in text:
            nodes.append(
                _comparison(
                    ScannerField.MA_BULLISH,
                    ComparisonOperator.EQ,
                    True,
                )
            )
        if "均线空头" in text or "空头排列" in text:
            nodes.append(
                _comparison(
                    ScannerField.MA_BEARISH,
                    ComparisonOperator.EQ,
                    True,
                )
            )
        if "技术面为正" in text or "技术分为正" in text:
            nodes.append(
                _comparison(
                    ScannerField.TECHNICAL_SCORE,
                    ComparisonOperator.GT,
                    0.0,
                )
            )
        if "基本面为正" in text or "基本面分数为正" in text:
            nodes.append(
                _comparison(
                    ScannerField.FUNDAMENTAL_SCORE,
                    ComparisonOperator.GT,
                    0.0,
                )
            )
        if "情绪面为正" in text or "情绪分为正" in text:
            nodes.append(
                _comparison(
                    ScannerField.SENTIMENT_SCORE,
                    ComparisonOperator.GT,
                    0.0,
                )
            )
        if "政策消息面为正" in text or "政策分为正" in text:
            nodes.append(
                _comparison(
                    ScannerField.POLICY_NEWS_SCORE,
                    ComparisonOperator.GT,
                    0.0,
                )
            )
        if "资金面为正" in text or "资金分为正" in text:
            nodes.append(
                _comparison(
                    ScannerField.CAPITAL_FLOW_SCORE,
                    ComparisonOperator.GT,
                    0.0,
                )
            )
        for match in re.finditer(r"至少([1-5])(?:个因子|/5因子|维因子)", text):
            nodes.append(
                _comparison(
                    ScannerField.FACTOR_COVERAGE_COUNT,
                    ComparisonOperator.GTE,
                    int(match.group(1)),
                )
            )
        if "排除低综合置信度" in text:
            nodes.append(
                _comparison(
                    ScannerField.COMPOSITE_CONFIDENCE,
                    ComparisonOperator.GTE,
                    0.35,
                )
            )
        for match in re.finditer(r"存在([a-z_]{3,64})风险(?:标志)?", text):
            nodes.append(
                MembershipNode(
                    field=ScannerField.RISK_FLAGS,
                    operator=MembershipOperator.CONTAINS,
                    values=[match.group(1).upper()],
                )
            )
        if "macd为正" in text or "macd金叉" in text:
            nodes.append(
                _comparison(
                    ScannerField.MACD_STATE,
                    ComparisonOperator.EQ,
                    "POSITIVE",
                )
            )
        if "macd为负" in text or "macd死叉" in text:
            nodes.append(
                _comparison(
                    ScannerField.MACD_STATE,
                    ComparisonOperator.EQ,
                    "NEGATIVE",
                )
            )
        if "上涨趋势" in text or "均线多头" in text:
            nodes.append(
                _comparison(
                    ScannerField.MA_BULLISH,
                    ComparisonOperator.EQ,
                    True,
                )
            )
        for match in re.finditer(r"连续上涨(\d+)日", text):
            nodes.append(
                _comparison(
                    ScannerField.CONSECUTIVE_UP_DAYS,
                    ComparisonOperator.GTE,
                    int(match.group(1)),
                )
            )
        for match in re.finditer(r"连续下跌(\d+)日", text):
            nodes.append(
                _comparison(
                    ScannerField.CONSECUTIVE_DOWN_DAYS,
                    ComparisonOperator.GTE,
                    int(match.group(1)),
                )
            )
        unique: list[FilterNode] = []
        seen: set[str] = set()
        for node in nodes:
            key = node.model_dump_json()
            if key not in seen:
                seen.add(key)
                unique.append(node)
        return unique

    @classmethod
    def _filters(cls, normalized: str) -> list[FilterNode]:
        segments = re.split(r"或者|或", normalized)
        groups: list[FilterNode] = []
        for segment in segments:
            nodes = cls._condition_nodes(segment)
            if not nodes:
                continue
            groups.append(
                nodes[0]
                if len(nodes) == 1
                else BooleanNode(
                    operator=BooleanOperator.AND,
                    children=nodes,
                )
            )
        if len(groups) <= 1:
            return groups
        return [BooleanNode(operator=BooleanOperator.OR, children=groups)]

    @staticmethod
    def _contains_forbidden_model_output(value: Any) -> bool:
        if isinstance(value, dict):
            if any(str(key).casefold() in _TRADE_OUTPUT_KEYS for key in value):
                return True
            return any(
                LocalChineseQueryParser._contains_forbidden_model_output(item)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                LocalChineseQueryParser._contains_forbidden_model_output(item)
                for item in value
            )
        return False

    def _model_fallback(
        self,
        request: ScannerParseRequest,
        *,
        local_response: ScannerParseResponse,
    ) -> ScannerParseResponse:
        if not request.allow_parser_model or self.model_parser is None:
            return local_response
        try:
            raw = self.model_parser(request.query)
            payload = json.loads(raw) if isinstance(raw, str) else raw
            if self._contains_forbidden_model_output(payload):
                raise ValueError("model parser returned a trading field")
            values = dict(payload)
            values.update(
                {
                    "original_query": request.query,
                    "normalized_query": _normalize(request.query),
                    "analysis_mode": request.analysis_mode,
                    "data_cutoff": request.data_cutoff,
                    "top_n": request.top_n or values.get("top_n", 20),
                    "missing_data_policy": request.missing_data_policy,
                    "freshness_policy": request.freshness_policy,
                    "parser_type": ParserType.MODEL,
                    "parser_version": MODEL_PARSER_VERSION,
                    "generated_at": datetime.now().astimezone(),
                }
            )
            values["plan_hash"] = stable_plan_hash(values)
            values["query_id"] = "sq_" + values["plan_hash"][:24]
            plan = ScannerQueryPlan.model_validate(values)
            for node in plan.filters:
                validate_filter(node)
            return ScannerParseResponse(
                parsed_query=plan,
                condition_summary=["模型仅用于一次查询计划解析，扫描仍为本地确定性执行"],
                clarification_required=False,
                parser_model_call_count=1,
                risk_flags=[ScannerRiskFlag.NOT_A_TRADE_RECOMMENDATION],
            )
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError):
            return local_response.model_copy(
                update={
                    "parser_model_call_count": 1,
                    "risk_flags": list(
                        dict.fromkeys(
                            [
                                *local_response.risk_flags,
                                ScannerRiskFlag.MODEL_PARSER_FAILED,
                            ]
                        )
                    ),
                }
            )

    def parse(self, request: ScannerParseRequest) -> ScannerParseResponse:
        normalized = _normalize(request.query)
        try:
            ensure_safe_text(normalized)
        except ValueError:
            return ScannerParseResponse(
                parsed_query=None,
                condition_summary=[],
                clarification_required=True,
                clarification_questions=["查询包含禁止的SQL或Python表达式，请改用自然语言条件。"],
                unsupported_fragments=["FORBIDDEN_EXECUTABLE_TOKEN"],
                risk_flags=[
                    ScannerRiskFlag.QUERY_FIELD_UNSUPPORTED,
                    ScannerRiskFlag.NOT_A_TRADE_RECOMMENDATION,
                ],
            )

        include_boards: list[str] = []
        exclude_boards: list[str] = []
        for alias, board in BOARD_ALIASES.items():
            if alias not in normalized:
                continue
            if f"排除{alias}" in normalized or f"不含{alias}" in normalized:
                exclude_boards.append(board)
            else:
                include_boards.append(board)

        include_symbols: list[str] = []
        exclude_symbols: list[str] = []
        for match in _SYMBOL.finditer(normalized):
            code, suffix = match.groups()
            if suffix:
                symbol = f"{code}.{suffix.upper()}"
            elif code.startswith(("4", "8", "9")):
                symbol = f"{code}.BJ"
            elif code.startswith("6"):
                symbol = f"{code}.SH"
            else:
                symbol = f"{code}.SZ"
            prefix = normalized[max(0, match.start() - 4) : match.start()]
            if "排除" in prefix:
                exclude_symbols.append(symbol)
            else:
                include_symbols.append(symbol)

        filters = self._filters(normalized)
        if "排除st" in normalized or "非st" in normalized:
            filters.append(
                _comparison(
                    ScannerField.IS_ST,
                    ComparisonOperator.EQ,
                    False,
                )
            )
        if (
            "排除停牌" in normalized
            or "排除st和停牌" in normalized
            or "非停牌" in normalized
        ):
            filters.append(
                _comparison(
                    ScannerField.IS_SUSPENDED,
                    ComparisonOperator.EQ,
                    False,
                )
            )
        for match in re.finditer(r"排除上市不足(\d+)日(?:的新股)?", normalized):
            filters.append(
                _comparison(
                    ScannerField.LISTING_AGE_DAYS,
                    ComparisonOperator.GTE,
                    int(match.group(1)),
                )
            )

        anomaly_phrases = {
            "放量": AnomalyType.VOLUME_SPIKE,
            "缩量": AnomalyType.LIQUIDITY_DROP,
            "量价齐升": AnomalyType.PRICE_UP_VOLUME_UP,
            "放量上涨": AnomalyType.PRICE_UP_VOLUME_UP,
            "价涨量缩": AnomalyType.PRICE_UP_VOLUME_DOWN,
            "价跌量增": AnomalyType.PRICE_DOWN_VOLUME_UP,
            "价跌量缩": AnomalyType.PRICE_DOWN_VOLUME_DOWN,
            "突破": AnomalyType.BREAKOUT_HIGH,
            "新高": AnomalyType.BREAKOUT_HIGH,
            "跌破": AnomalyType.BREAKDOWN_LOW,
            "新低": AnomalyType.BREAKDOWN_LOW,
            "高换手": AnomalyType.HIGH_TURNOVER,
            "换手率高于配置阈值": AnomalyType.HIGH_TURNOVER,
            "低流动性": AnomalyType.LIQUIDITY_DROP,
            "接近涨停": AnomalyType.NEAR_PRICE_LIMIT,
            "涨停": AnomalyType.PRICE_LIMIT_UP,
            "跌停": AnomalyType.PRICE_LIMIT_DOWN,
        }
        anomalies = [
            anomaly
            for phrase, anomaly in anomaly_phrases.items()
            if phrase in normalized
        ]
        anomalies = list(dict.fromkeys(anomalies))

        sort_map = {
            "按涨幅": ScannerField.CHANGE_PCT,
            "按成交额": ScannerField.AMOUNT,
            "按量比": ScannerField.VOLUME_RATIO_5D,
            "按换手率": ScannerField.TURNOVER_RATE,
            "按技术分": ScannerField.TECHNICAL_SCORE,
            "按资金分": ScannerField.CAPITAL_FLOW_SCORE,
            "按影子综合分": ScannerField.SHADOW_COMPOSITE_SCORE,
        }
        sort_fields = [
            SortNode(field=field, direction=SortDirection.DESC)
            for phrase, field in sort_map.items()
            if phrase in normalized
        ]
        top_match = re.search(r"(?:前|top)(10|20|30)只?", normalized)
        top_n = (
            int(top_match.group(1))
            if top_match
            else request.top_n
            if request.top_n is not None
            else 20
        )

        include_industries: list[str] = []
        exclude_industries: list[str] = []
        industry_match = re.search(r"(?:属于|指定|筛选)([^,，且或]{2,20})行业", normalized)
        if industry_match:
            include_industries.append(industry_match.group(1))
        concise_industry = re.search(
            r"只看([^,，且或]{2,20}?)(?:行业|板块)?(?:$|,)",
            normalized,
        )
        if concise_industry:
            include_industries.append(concise_industry.group(1))
        excluded_industry = re.search(r"排除([^,，且或]{2,20})行业", normalized)
        if excluded_industry:
            exclude_industries.append(excluded_industry.group(1))

        ambiguity: list[ScannerRiskFlag] = []
        questions: list[str] = []
        unsupported: list[str] = []
        if re.search(rf"成交额(?:大于|超过|小于|低于){_NUMBER}(?![万亿元])", normalized):
            ambiguity.append(ScannerRiskFlag.QUERY_AMBIGUOUS)
            questions.append("成交额缺少元、万元或亿元单位。")
        if re.search(rf"(?:价格|股价)(?:大于|小于|超过|低于){_NUMBER}(?!元)", normalized):
            ambiguity.append(ScannerRiskFlag.QUERY_AMBIGUOUS)
            questions.append("价格缺少元单位。")
        if re.search(
            rf"换手率(?:大于|小于|超过|低于){_NUMBER}(?!%)",
            normalized,
        ):
            ambiguity.append(ScannerRiskFlag.QUERY_AMBIGUOUS)
            questions.append("换手率缺少百分比单位。")
        unsupported_markers = {
            "市盈率": "市盈率",
            "市净率": "市净率",
            "目标价": "目标价",
            "预测涨停": "预测涨停",
            "建议买入": "交易建议",
        }
        for marker, label in unsupported_markers.items():
            if marker in normalized:
                unsupported.append(label)
        if unsupported:
            ambiguity.append(ScannerRiskFlag.QUERY_FIELD_UNSUPPORTED)
            questions.append("查询包含当前白名单未支持的字段或交易表达。")

        generated_at = datetime.now().astimezone()
        values: dict[str, Any] = {
            "query_id": "sq_" + hashlib.sha256(normalized.encode()).hexdigest()[:24],
            "original_query": request.query,
            "normalized_query": normalized,
            "analysis_mode": request.analysis_mode,
            "data_cutoff": request.data_cutoff,
            "universe": "ALL_A_SHARES",
            "include_boards": list(dict.fromkeys(include_boards)),
            "exclude_boards": list(dict.fromkeys(exclude_boards)),
            "include_industries": include_industries,
            "exclude_industries": exclude_industries,
            "include_symbols": list(dict.fromkeys(include_symbols)),
            "exclude_symbols": list(dict.fromkeys(exclude_symbols)),
            "filters": filters,
            "anomaly_conditions": anomalies,
            "sort_fields": sort_fields,
            "top_n": top_n,
            "missing_data_policy": request.missing_data_policy,
            "freshness_policy": request.freshness_policy,
            "ambiguity_flags": list(dict.fromkeys(ambiguity)),
            "parser_type": ParserType.LOCAL,
            "parser_version": LOCAL_PARSER_VERSION,
            "generated_at": generated_at,
        }
        for node in filters:
            try:
                validate_filter(node)
            except ValueError as exc:
                questions.append(str(exc))
                ambiguity.append(ScannerRiskFlag.QUERY_VALUE_INVALID)
        values["ambiguity_flags"] = list(dict.fromkeys(ambiguity))
        values["plan_hash"] = stable_plan_hash(values)
        values["query_id"] = "sq_" + values["plan_hash"][:24]
        plan = ScannerQueryPlan.model_validate(values)
        summary = [
            f"范围：{','.join(plan.include_boards) if plan.include_boards else '全A股'}",
            (
                "行业："
                + (
                    ",".join(plan.include_industries)
                    if plan.include_industries
                    else "未限定"
                )
            ),
            f"硬过滤条件：{len(plan.filters)}项",
            f"异动条件：{','.join(item.value for item in plan.anomaly_conditions) or '无'}",
            f"返回：前{plan.top_n}只研究候选",
        ]
        response = ScannerParseResponse(
            parsed_query=plan,
            condition_summary=summary,
            clarification_required=bool(ambiguity),
            clarification_questions=list(dict.fromkeys(questions)),
            unsupported_fragments=unsupported,
            risk_flags=list(
                dict.fromkeys(
                    [
                        *ambiguity,
                        ScannerRiskFlag.NOT_A_TRADE_RECOMMENDATION,
                    ]
                )
            ),
        )
        if response.clarification_required:
            return self._model_fallback(request, local_response=response)
        return response


__all__ = ["LocalChineseQueryParser", "ParserModel"]
