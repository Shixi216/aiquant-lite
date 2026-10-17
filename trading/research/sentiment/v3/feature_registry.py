from __future__ import annotations

import hashlib
from collections import Counter
from enum import StrEnum

from pydantic import Field, model_validator

from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.schemas import SnapshotModel


FEATURE_REGISTRY_VERSION = "Sentiment_V3_Feature_Registry_V1"
ASSEMBLY_ALGORITHM_VERSION = "sentiment-v3-feature-assembly-s2e-1.0.0"
DATASET_VERSION = "sentiment-v3-research-dataset-s2e-1.0.0"


class FeatureDataType(StrEnum):
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    STRING = "STRING"
    JSON = "JSON"


class ResearchStatus(StrEnum):
    READY_FOR_HISTORY = "READY_FOR_HISTORY"
    FORWARD_ONLY = "FORWARD_ONLY"
    PIT_UNSAFE_FOR_HISTORY = "PIT_UNSAFE_FOR_HISTORY"
    EOD_ONLY = "EOD_ONLY"
    CURRENT_MEMBERSHIP_ONLY = "CURRENT_MEMBERSHIP_ONLY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    VENDOR_DERIVED = "VENDOR_DERIVED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class CandidateRole(StrEnum):
    ALPHA_CANDIDATE = "ALPHA_CANDIDATE"
    CONTEXT_CANDIDATE = "CONTEXT_CANDIDATE"
    CONFIDENCE_CANDIDATE = "CONFIDENCE_CANDIDATE"
    RISK_CANDIDATE = "RISK_CANDIDATE"
    VETO_CANDIDATE = "VETO_CANDIDATE"
    MONITOR_ONLY = "MONITOR_ONLY"
    UNKNOWN = "UNKNOWN"


class FeatureDefinition(SnapshotModel):
    feature_name: str = Field(pattern=r"^[a-z][a-z0-9_.]*$")
    module: str
    data_type: FeatureDataType
    description: str
    source_snapshot_type: str
    source_field: str
    methodology_class: str
    unit: str
    algorithm_version: str
    pit_requirement: str = "AVAILABLE_TIME_LTE_CUTOFF"
    finality_requirement: str
    availability_requirement: str = "NOT_STALE_AND_PIT_VISIBLE"
    historical_support: list[ResearchStatus]
    candidate_role: CandidateRole = CandidateRole.UNKNOWN
    canonical_feature: str | None = None
    formal_weight: float = 0.0

    @model_validator(mode="after")
    def validate_alias(self) -> "FeatureDefinition":
        if self.canonical_feature == self.feature_name:
            raise ValueError("feature cannot alias itself")
        return self


class FeatureRegistry(SnapshotModel):
    registry_version: str
    definitions: list[FeatureDefinition]
    content_hash: str

    @model_validator(mode="after")
    def validate_registry(self) -> "FeatureRegistry":
        names = [item.feature_name for item in self.definitions]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        known = set(names)
        for item in self.definitions:
            if item.canonical_feature and item.canonical_feature not in known:
                raise ValueError(f"unknown canonical feature: {item.canonical_feature}")
        expected = _registry_hash(self.registry_version, self.definitions)
        if self.content_hash != expected:
            raise ValueError("feature registry content hash mismatch")
        return self

    def canonical_definitions(self) -> list[FeatureDefinition]:
        return [item for item in self.definitions if item.canonical_feature is None]

    def history_safe(self) -> list[FeatureDefinition]:
        excluded = {
            ResearchStatus.FORWARD_ONLY,
            ResearchStatus.PIT_UNSAFE_FOR_HISTORY,
            ResearchStatus.CURRENT_MEMBERSHIP_ONLY,
            ResearchStatus.NOT_IMPLEMENTED,
        }
        return [
            item
            for item in self.canonical_definitions()
            if ResearchStatus.READY_FOR_HISTORY in item.historical_support
            and not (set(item.historical_support) & excluded)
        ]

    def forward_shadow(self) -> list[FeatureDefinition]:
        return [
            item
            for item in self.canonical_definitions()
            if ResearchStatus.NOT_IMPLEMENTED not in item.historical_support
        ]

    def stats(self) -> dict[str, object]:
        modules = Counter(item.module.split(".")[0] for item in self.definitions)
        statuses = Counter(
            status.value for item in self.definitions for status in item.historical_support
        )
        return {
            "total": len(self.definitions),
            "canonical": len(self.canonical_definitions()),
            "aliases": len(self.definitions) - len(self.canonical_definitions()),
            "modules": dict(sorted(modules.items())),
            "statuses": dict(sorted(statuses.items())),
            "history_safe": len(self.history_safe()),
            "forward_shadow": len(self.forward_shadow()),
        }


def _registry_hash(version: str, definitions: list[FeatureDefinition]) -> str:
    payload = {
        "registry_version": version,
        "definitions": [item.model_dump(mode="json") for item in definitions],
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _definition(
    name: str,
    source_type: str,
    source_field: str,
    *,
    data_type: FeatureDataType = FeatureDataType.FLOAT,
    methodology: str = "DERIVED_TRANSPARENT",
    unit: str = "RATIO",
    statuses: tuple[ResearchStatus, ...] = (
        ResearchStatus.READY_FOR_HISTORY,
        ResearchStatus.EOD_ONLY,
    ),
    canonical: str | None = None,
) -> FeatureDefinition:
    module = ".".join(name.split(".")[:-1])
    return FeatureDefinition(
        feature_name=name,
        module=module,
        data_type=data_type,
        description=f"Research candidate sourced from {source_type}.{source_field}",
        source_snapshot_type=source_type,
        source_field=source_field,
        methodology_class=methodology,
        unit=unit,
        algorithm_version=ASSEMBLY_ALGORITHM_VERSION,
        finality_requirement="FINAL_OR_CORRECTED_EOD",
        historical_support=list(statuses),
        canonical_feature=canonical,
    )


def _field_definitions() -> list[FeatureDefinition]:
    ready = (ResearchStatus.READY_FOR_HISTORY, ResearchStatus.EOD_ONLY)
    forward = (ResearchStatus.FORWARD_ONLY, ResearchStatus.EOD_ONLY)
    membership = (
        ResearchStatus.FORWARD_ONLY,
        ResearchStatus.EOD_ONLY,
        ResearchStatus.CURRENT_MEMBERSHIP_ONLY,
        ResearchStatus.PIT_UNSAFE_FOR_HISTORY,
    )
    vendor = (
        ResearchStatus.FORWARD_ONLY,
        ResearchStatus.EOD_ONLY,
        ResearchStatus.VENDOR_DERIVED,
    )
    result: list[FeatureDefinition] = []

    market_fields = {
        "advancers": (FeatureDataType.INTEGER, "COUNT"),
        "decliners": (FeatureDataType.INTEGER, "COUNT"),
        "unchanged": (FeatureDataType.INTEGER, "COUNT"),
        "advance_ratio": (FeatureDataType.FLOAT, "RATIO"),
        "decline_ratio": (FeatureDataType.FLOAT, "RATIO"),
        "advance_decline_ratio": (FeatureDataType.FLOAT, "RATIO"),
        "breadth_strength": (FeatureDataType.FLOAT, "RATIO"),
        "limit_up_count": (FeatureDataType.INTEGER, "COUNT"),
        "limit_down_count": (FeatureDataType.INTEGER, "COUNT"),
        "limit_up_down_ratio": (FeatureDataType.FLOAT, "RATIO"),
        "broken_board_count": (FeatureDataType.INTEGER, "COUNT"),
        "broken_board_rate": (FeatureDataType.FLOAT, "RATIO"),
        "strong_stock_count": (FeatureDataType.INTEGER, "COUNT"),
        "market_turnover": (FeatureDataType.FLOAT, "CNY"),
        "market_turnover_change": (FeatureDataType.FLOAT, "RATIO"),
        "coverage_ratio": (FeatureDataType.FLOAT, "RATIO"),
    }
    for field, (kind, unit) in market_fields.items():
        status = (
            forward
            if field
            in {
                "limit_up_count",
                "limit_down_count",
                "limit_up_down_ratio",
                "broken_board_count",
                "broken_board_rate",
                "strong_stock_count",
            }
            else ready
        )
        result.append(
            _definition(
                f"market.{field}", "MARKET", field, data_type=kind, unit=unit, statuses=status
            )
        )

    speculation_fields = {
        "first_board_count": (FeatureDataType.INTEGER, "COUNT"),
        "second_board_count": (FeatureDataType.INTEGER, "COUNT"),
        "third_board_count": (FeatureDataType.INTEGER, "COUNT"),
        "high_board_count": (FeatureDataType.INTEGER, "COUNT"),
        "max_board_height": (FeatureDataType.INTEGER, "COUNT"),
        "sealed_limit_up_count": (FeatureDataType.INTEGER, "COUNT"),
        "promotion_rate": (FeatureDataType.FLOAT, "RATIO"),
        "yesterday_limit_up_avg_return": (FeatureDataType.FLOAT, "RETURN"),
        "yesterday_limit_up_median_return": (FeatureDataType.FLOAT, "RETURN"),
        "yesterday_board_success_rate": (FeatureDataType.FLOAT, "RATIO"),
        "broken_board_repair_rate": (FeatureDataType.FLOAT, "RATIO"),
        "high_position_negative_feedback": (FeatureDataType.FLOAT, "RATIO"),
    }
    for field, (kind, unit) in speculation_fields.items():
        result.append(
            _definition(
                f"speculation.{field}",
                "SPECULATION",
                field,
                data_type=kind,
                unit=unit,
                statuses=forward,
            )
        )
    for field in ("broken_board_count", "broken_board_rate", "strong_stock_count"):
        result.append(
            _definition(
                f"speculation.{field}",
                "SPECULATION",
                field,
                data_type=FeatureDataType.INTEGER
                if field.endswith("count")
                else FeatureDataType.FLOAT,
                unit="COUNT" if field.endswith("count") else "RATIO",
                statuses=forward,
                canonical=f"market.{field}",
            )
        )

    sector_fields = {
        "advance_ratio": "RATIO",
        "mean_return": "RETURN",
        "median_return": "RETURN",
        "turnover_change": "RATIO",
        "volume_change": "RATIO",
        "limit_up_count": "COUNT",
        "limit_down_count": "COUNT",
        "broken_board_count": "COUNT",
        "limit_up_ratio": "RATIO",
        "broken_board_rate": "RATIO",
        "relative_market_return": "RETURN",
        "relative_market_breadth": "RATIO",
        "leader_return": "RETURN",
        "top_n_mean_return": "RETURN",
        "top_n_median_return": "RETURN",
        "coverage_ratio": "RATIO",
    }
    for field, unit in sector_fields.items():
        result.append(
            _definition(
                f"sector.{field}",
                "SECTOR",
                field,
                data_type=FeatureDataType.INTEGER if unit == "COUNT" else FeatureDataType.FLOAT,
                unit=unit,
                statuses=membership,
            )
        )

    stock_fields = {
        "return_pct": "RETURN",
        "open_gap_pct": "RETURN",
        "intraday_return_pct": "RETURN",
        "amplitude": "RATIO",
        "turnover_rate": "RATIO",
        "volume_ratio": "RATIO",
        "volume_change": "RATIO",
        "turnover_change": "RATIO",
        "close_location_value": "RATIO",
        "high_to_close_drawdown": "RETURN",
        "low_to_close_recovery": "RETURN",
        "gap_and_fade": "RETURN",
        "gap_and_follow_through": "RETURN",
        "relative_market_return": "RETURN",
        "relative_sector_return": "RETURN",
        "relative_market_volume": "RATIO",
        "relative_sector_volume": "RATIO",
        "is_limit_up": "BOOLEAN",
        "is_limit_down": "BOOLEAN",
        "is_broken_board": "BOOLEAN",
    }
    for field, unit in stock_fields.items():
        status = membership if "sector" in field else ready
        result.append(
            _definition(
                f"stock.{field}",
                "STOCK",
                field,
                data_type=FeatureDataType.BOOLEAN if unit == "BOOLEAN" else FeatureDataType.FLOAT,
                methodology="OBSERVABLE_FACT" if field.startswith("is_") else "DERIVED_TRANSPARENT",
                unit=unit,
                statuses=status,
            )
        )

    capital_fields: dict[str, tuple[str, str, str, tuple[ResearchStatus, ...], str | None]] = {
        "capital.market.market_turnover": (
            "MARKET_CAPITAL",
            "market_turnover",
            "CNY",
            ready,
            "market.market_turnover",
        ),
        "capital.market.market_turnover_change": (
            "MARKET_CAPITAL",
            "market_turnover_change",
            "RATIO",
            ready,
            "market.market_turnover_change",
        ),
        "capital.market.vendor_net_flow": (
            "MARKET_CAPITAL",
            "vendor_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.market.vendor_main_net_flow": (
            "MARKET_CAPITAL",
            "vendor_main_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.market.vendor_main_flow_ratio": (
            "MARKET_CAPITAL",
            "vendor_main_flow_ratio",
            "RATIO",
            vendor,
            None,
        ),
        "capital.sector.turnover_change": (
            "SECTOR_CAPITAL",
            "turnover_change",
            "RATIO",
            membership,
            "sector.turnover_change",
        ),
        "capital.sector.vendor_main_flow_sum": (
            "SECTOR_CAPITAL",
            "vendor_main_flow_sum",
            "CNY",
            membership + (ResearchStatus.VENDOR_DERIVED,),
            None,
        ),
        "capital.sector.vendor_main_flow_median": (
            "SECTOR_CAPITAL",
            "vendor_main_flow_median",
            "CNY",
            membership + (ResearchStatus.VENDOR_DERIVED,),
            None,
        ),
        "capital.sector.vendor_large_flow_sum": (
            "SECTOR_CAPITAL",
            "vendor_large_flow_sum",
            "CNY",
            membership + (ResearchStatus.VENDOR_DERIVED,),
            None,
        ),
        "capital.sector.vendor_small_flow_sum": (
            "SECTOR_CAPITAL",
            "vendor_small_flow_sum",
            "CNY",
            membership + (ResearchStatus.VENDOR_DERIVED,),
            None,
        ),
        "capital.sector.relative_market_turnover": (
            "SECTOR_CAPITAL",
            "relative_market_turnover",
            "RATIO",
            membership,
            None,
        ),
        "capital.sector.relative_market_flow": (
            "SECTOR_CAPITAL",
            "relative_market_flow",
            "RATIO",
            membership + (ResearchStatus.VENDOR_DERIVED,),
            None,
        ),
        "capital.stock.turnover": ("STOCK_CAPITAL", "turnover", "CNY", ready, None),
        "capital.stock.turnover_change": (
            "STOCK_CAPITAL",
            "turnover_change",
            "RATIO",
            ready,
            "stock.turnover_change",
        ),
        "capital.stock.turnover_rate": (
            "STOCK_CAPITAL",
            "turnover_rate",
            "RATIO",
            ready,
            "stock.turnover_rate",
        ),
        "capital.stock.vendor_main_net_flow": (
            "STOCK_CAPITAL",
            "vendor_main_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.stock.vendor_super_large_net_flow": (
            "STOCK_CAPITAL",
            "vendor_super_large_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.stock.vendor_large_net_flow": (
            "STOCK_CAPITAL",
            "vendor_large_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.stock.vendor_medium_net_flow": (
            "STOCK_CAPITAL",
            "vendor_medium_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.stock.vendor_small_net_flow": (
            "STOCK_CAPITAL",
            "vendor_small_net_flow",
            "CNY",
            vendor,
            None,
        ),
        "capital.stock.vendor_main_flow_ratio": (
            "STOCK_CAPITAL",
            "vendor_main_flow_ratio",
            "RATIO",
            vendor,
            None,
        ),
        "capital.stock.relative_market_flow": (
            "STOCK_CAPITAL",
            "relative_market_flow",
            "RATIO",
            vendor,
            None,
        ),
        "capital.stock.relative_sector_flow": (
            "STOCK_CAPITAL",
            "relative_sector_flow",
            "RATIO",
            vendor
            + (ResearchStatus.CURRENT_MEMBERSHIP_ONLY, ResearchStatus.PIT_UNSAFE_FOR_HISTORY),
            None,
        ),
        "capital.margin.margin_balance": ("MARGIN_FINANCING", "margin_balance", "CNY", ready, None),
        "capital.margin.margin_buy": ("MARGIN_FINANCING", "margin_buy", "CNY", ready, None),
        "capital.margin.margin_repay": ("MARGIN_FINANCING", "margin_repay", "CNY", ready, None),
        "capital.margin.margin_net_buy": ("MARGIN_FINANCING", "margin_net_buy", "CNY", ready, None),
        "capital.margin.margin_balance_change": (
            "MARGIN_FINANCING",
            "margin_balance_change",
            "CNY",
            ready,
            None,
        ),
        "capital.margin.margin_balance_change_pct": (
            "MARGIN_FINANCING",
            "margin_balance_change_pct",
            "RATIO",
            ready,
            None,
        ),
        "capital.dragon_tiger.has_dragon_tiger": (
            "DRAGON_TIGER",
            "has_dragon_tiger",
            "BOOLEAN",
            ready,
            None,
        ),
        "capital.dragon_tiger.net_amount": ("DRAGON_TIGER", "net_amount", "CNY", ready, None),
        "capital.dragon_tiger.institution_net_amount": (
            "DRAGON_TIGER",
            "institution_net_amount",
            "CNY",
            ready,
            None,
        ),
        "capital.dragon_tiger.institution_seat_count": (
            "DRAGON_TIGER",
            "institution_seat_count",
            "COUNT",
            ready,
            None,
        ),
    }
    for name, (source_type, field, unit, statuses, canonical) in capital_fields.items():
        methodology = (
            "VENDOR_DERIVED"
            if ResearchStatus.VENDOR_DERIVED in statuses
            else ("EVENT_CAPITAL" if source_type == "DRAGON_TIGER" else "OBSERVABLE_FACT")
        )
        kind = (
            FeatureDataType.BOOLEAN
            if unit == "BOOLEAN"
            else (FeatureDataType.INTEGER if unit == "COUNT" else FeatureDataType.FLOAT)
        )
        result.append(
            _definition(
                name,
                source_type,
                field,
                data_type=kind,
                methodology=methodology,
                unit=unit,
                statuses=statuses,
                canonical=canonical,
            )
        )

    news_fields = {
        "opinion_count",
        "expectation_change_count",
        "market_reaction_text_count",
        "rumor_count",
        "positive_count",
        "negative_count",
        "neutral_count",
        "mixed_count",
        "confidence_median",
    }
    for field in sorted(news_fields):
        result.append(
            _definition(
                f"news.{field}",
                "NEWS",
                field,
                data_type=FeatureDataType.FLOAT
                if field == "confidence_median"
                else FeatureDataType.INTEGER,
                methodology="TEXT_EXTRACTION",
                unit="RATIO" if field == "confidence_median" else "COUNT",
                statuses=(ResearchStatus.FORWARD_ONLY, ResearchStatus.INSUFFICIENT_HISTORY),
            )
        )
    for field, source_field in (
        ("document_count", "document_count"),
        ("source_count", "source_count"),
        ("novel_count", "novel_document_count"),
    ):
        result.append(
            _definition(
                f"attention.{field}",
                "NEWS",
                source_field,
                data_type=FeatureDataType.INTEGER,
                methodology="ATTENTION_PROXY",
                unit="COUNT",
                statuses=(ResearchStatus.FORWARD_ONLY, ResearchStatus.INSUFFICIENT_HISTORY),
            )
        )

    event_fields = {
        "event_count",
        "new_event_count",
        "risk_event_count",
        "official_confirmed_count",
        "positive_count",
        "negative_count",
        "mixed_count",
        "event_type_counts",
    }
    for field in sorted(event_fields):
        result.append(
            _definition(
                f"event.{field}",
                "EVENT_INTELLIGENCE",
                field,
                data_type=FeatureDataType.JSON
                if field.endswith("counts")
                else FeatureDataType.INTEGER,
                methodology="EVENT_AGGREGATE",
                unit="JSON" if field.endswith("counts") else "COUNT",
                statuses=(ResearchStatus.FORWARD_ONLY, ResearchStatus.INSUFFICIENT_HISTORY),
            )
        )

    reaction_fields = {
        "reaction_count": "COUNT",
        "stock_return": "RETURN",
        "market_return": "RETURN",
        "sector_return": "RETURN",
        "relative_market_return": "RETURN",
        "relative_sector_return": "RETURN",
        "volume_change": "RATIO",
        "turnover_change": "RATIO",
        "close_location_value": "RATIO",
        "open_gap_pct": "RETURN",
        "high_to_close_drawdown": "RETURN",
        "low_to_close_recovery": "RETURN",
    }
    for field, unit in reaction_fields.items():
        result.append(
            _definition(
                f"reaction.{field}",
                "EVENT_REACTION",
                field,
                data_type=FeatureDataType.INTEGER if unit == "COUNT" else FeatureDataType.FLOAT,
                methodology="RESEARCH_CLASSIFICATION"
                if field == "reaction_count"
                else "DERIVED_TRANSPARENT",
                unit=unit,
                statuses=(ResearchStatus.FORWARD_ONLY, ResearchStatus.EOD_ONLY),
            )
        )
    return result


def build_feature_registry() -> FeatureRegistry:
    definitions = sorted(_field_definitions(), key=lambda item: item.feature_name)
    return FeatureRegistry(
        registry_version=FEATURE_REGISTRY_VERSION,
        definitions=definitions,
        content_hash=_registry_hash(FEATURE_REGISTRY_VERSION, definitions),
    )


FEATURE_REGISTRY_V1 = build_feature_registry()


__all__ = [
    "ASSEMBLY_ALGORITHM_VERSION",
    "CandidateRole",
    "DATASET_VERSION",
    "FEATURE_REGISTRY_V1",
    "FEATURE_REGISTRY_VERSION",
    "FeatureDataType",
    "FeatureDefinition",
    "FeatureRegistry",
    "ResearchStatus",
    "build_feature_registry",
]
