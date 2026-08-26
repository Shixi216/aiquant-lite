from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from trading.research.sentiment.v3.feature_registry import (
    ASSEMBLY_ALGORITHM_VERSION,
    DATASET_VERSION,
    FEATURE_REGISTRY_V1,
    FEATURE_REGISTRY_VERSION,
    FeatureDefinition,
    FeatureRegistry,
    ResearchStatus,
)
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.schemas import (
    Finality,
    QualityFlag,
    RecordSource,
    SnapshotModel,
    SnapshotType,
)
from trading.research.sentiment.v3.text_models import (
    EventIntelligenceRecord,
    EventReactionSnapshot,
    Novelty,
    TextDirection,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class PacketType(StrEnum):
    EOD_RESEARCH_PACKET = "EOD_RESEARCH_PACKET"
    FORWARD_INTRADAY_PACKET = "FORWARD_INTRADAY_PACKET"


class PITStatus(StrEnum):
    PIT_SAFE = "PIT_SAFE"
    PIT_UNSAFE = "PIT_UNSAFE"


class PacketQualityStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class MissingReason(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_LISTED = "NOT_LISTED"
    NO_EVENT = "NO_EVENT"
    NO_DRAGON_TIGER = "NO_DRAGON_TIGER"
    NO_MARGIN_ELIGIBILITY = "NO_MARGIN_ELIGIBILITY"
    PIT_UNAVAILABLE = "PIT_UNAVAILABLE"
    REACTION_NOT_AVAILABLE_YET = "REACTION_NOT_AVAILABLE_YET"
    STALE = "STALE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class FeatureQuality(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    STALE = "STALE"
    PIT_UNSAFE = "PIT_UNSAFE"
    VENDOR_DERIVED = "VENDOR_DERIVED"
    INFERRED_TIME = "INFERRED_TIME"
    LOW_COVERAGE = "LOW_COVERAGE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_LISTED = "NOT_LISTED"


class DatasetCutoffPolicy(StrEnum):
    EOD = "EOD"
    FORWARD_SHADOW = "FORWARD_SHADOW"


class MissingModule(SnapshotModel):
    module: str
    reason: MissingReason
    detail: str | None = None


class SnapshotLineage(SnapshotModel):
    snapshot_id: str
    snapshot_type: str
    source: str
    source_timestamp: datetime
    available_time: datetime
    schema_version: str
    algorithm_version: str
    content_hash: str


class ResearchFeaturePacket(SnapshotModel):
    packet_id: str
    packet_type: PacketType
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    trade_date: date
    decision_time: datetime
    cutoff: datetime
    record_source: RecordSource
    universe_id: str | None = None
    universe_version: str | None = None
    market_snapshot_id: str | None = None
    speculation_snapshot_id: str | None = None
    sector_snapshot_id: str | None = None
    stock_snapshot_id: str | None = None
    market_capital_snapshot_id: str | None = None
    sector_capital_snapshot_id: str | None = None
    stock_capital_snapshot_id: str | None = None
    margin_snapshot_id: str | None = None
    dragon_tiger_snapshot_id: str | None = None
    news_snapshot_id: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    reaction_ids: list[str] = Field(default_factory=list)
    feature_schema_version: str = FEATURE_REGISTRY_VERSION
    assembly_algorithm_version: str = ASSEMBLY_ALGORITHM_VERSION
    pit_status: PITStatus
    quality_status: PacketQualityStatus
    missing_modules: list[MissingModule] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    features: dict[str, Any]
    feature_names: list[str]
    feature_quality: dict[str, list[FeatureQuality]]
    feature_missing_reasons: dict[str, MissingReason]
    feature_lineage: dict[str, SnapshotLineage]
    coverage: dict[str, float | None]
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_weight: float = 0.0
    research_only: bool = True

    @field_validator("decision_time", "cutoff", "created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("packet timestamps must include timezone")
        return value

    @model_validator(mode="after")
    def validate_packet(self) -> "ResearchFeaturePacket":
        if self.cutoff > self.decision_time:
            raise ValueError("cutoff cannot be later than decision_time")
        if self.pit_status != PITStatus.PIT_SAFE:
            raise ValueError("assembler must not persist PIT-unsafe packets")
        if len(self.feature_names) != len(set(self.feature_names)):
            raise ValueError("feature names must be unique")
        return self

    def flat_features(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def walk(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    walk(f"{prefix}.{key}" if prefix else key, nested)
            else:
                result[prefix] = value

        walk("", self.features)
        return result


class ResearchFeatureRow(SnapshotModel):
    packet_id: str
    symbol: str
    trade_date: date
    cutoff: datetime
    record_source: RecordSource
    universe_version: str | None = None
    features: dict[str, Any]
    feature_quality: dict[str, list[FeatureQuality]]


class ResearchDatasetDefinition(SnapshotModel):
    dataset_id: str
    dataset_version: str = DATASET_VERSION
    feature_registry_version: str = FEATURE_REGISTRY_VERSION
    assembly_algorithm_version: str = ASSEMBLY_ALGORITHM_VERSION
    date_from: date
    date_to: date
    cutoff_policy: DatasetCutoffPolicy
    universe_versions: list[str]
    feature_list: list[str]
    row_count: int = Field(ge=0)
    symbol_count: int = Field(ge=0)
    record_source: RecordSource
    include_history_ready_only: bool
    packet_ids: list[str]
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contains_labels: Literal[False] = False
    formal_weight: float = 0.0


class ResearchDataset(SnapshotModel):
    definition: ResearchDatasetDefinition
    rows: list[ResearchFeatureRow]


class FeatureRepository(Protocol):
    def load_snapshots_as_of(self, **kwargs: Any) -> list[Any]: ...
    def list_events_as_of(self, **kwargs: Any) -> list[EventIntelligenceRecord]: ...
    def list_event_reactions_as_of(self, **kwargs: Any) -> list[EventReactionSnapshot]: ...
    def save_feature_packets(self, packets: Iterable[ResearchFeaturePacket]) -> list[Any]: ...
    def save_research_dataset(self, dataset: ResearchDatasetDefinition) -> Any: ...


def _nested_set(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _packet_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _lineage(snapshot: Any) -> SnapshotLineage:
    meta = snapshot.meta
    return SnapshotLineage(
        snapshot_id=meta.snapshot_id,
        snapshot_type=meta.snapshot_type.value,
        source=meta.source,
        source_timestamp=meta.source_timestamp,
        available_time=meta.available_time,
        schema_version=meta.schema_version,
        algorithm_version=meta.algorithm_version,
        content_hash=meta.content_hash,
    )


class ResearchFeatureAssembler:
    """PIT-safe in-memory join over one bulk Snapshot load per cutoff."""

    def __init__(
        self,
        *,
        repository: FeatureRepository,
        registry: FeatureRegistry = FEATURE_REGISTRY_V1,
    ) -> None:
        self.repository = repository
        self.registry = registry

    def assemble(
        self,
        *,
        symbol: str,
        decision_time: datetime,
        cutoff: datetime | None = None,
        trade_date: date | None = None,
        record_source: RecordSource = RecordSource.REAL,
        packet_type: PacketType = PacketType.EOD_RESEARCH_PACKET,
        include_history_ready_only: bool = False,
        persist: bool = False,
    ) -> ResearchFeaturePacket:
        return self.assemble_many(
            symbols=[symbol],
            decision_time=decision_time,
            cutoff=cutoff,
            trade_date=trade_date,
            record_source=record_source,
            packet_type=packet_type,
            include_history_ready_only=include_history_ready_only,
            persist=persist,
        )[0]

    def assemble_many(
        self,
        *,
        symbols: Iterable[str],
        decision_time: datetime,
        cutoff: datetime | None = None,
        trade_date: date | None = None,
        record_source: RecordSource = RecordSource.REAL,
        packet_type: PacketType = PacketType.EOD_RESEARCH_PACKET,
        include_history_ready_only: bool = False,
        persist: bool = False,
    ) -> list[ResearchFeaturePacket]:
        values = sorted(set(symbols))
        if not values:
            return []
        effective_cutoff = cutoff or decision_time
        if effective_cutoff.tzinfo is None or decision_time.tzinfo is None:
            raise ValueError("decision_time and cutoff must include timezone")
        if effective_cutoff > decision_time:
            raise ValueError("cutoff cannot be later than decision_time")
        packet_trade_date = trade_date or effective_cutoff.astimezone(SHANGHAI).date()
        finalities = (
            [Finality.FINAL, Finality.CORRECTED]
            if packet_type == PacketType.EOD_RESEARCH_PACKET
            else [Finality.INTRADAY, Finality.PRELIMINARY]
        )
        snapshots = self.repository.load_snapshots_as_of(
            cutoff=effective_cutoff,
            record_source=record_source,
            finalities=finalities,
        )
        snapshot_map = {
            (item.meta.snapshot_type.value, item.meta.symbol_scope): item for item in snapshots
        }
        events = self.repository.list_events_as_of(
            cutoff=effective_cutoff, symbols=values, record_source=record_source
        )
        reactions = self.repository.list_event_reactions_as_of(
            cutoff=effective_cutoff, symbols=values, record_source=record_source
        )
        events_by_symbol: dict[str, list[EventIntelligenceRecord]] = defaultdict(list)
        for item in events:
            if item.available_time <= effective_cutoff and (
                item.available_time.astimezone(SHANGHAI).date() == packet_trade_date
            ):
                events_by_symbol[item.symbol].append(item)
        reactions_by_symbol: dict[str, list[EventReactionSnapshot]] = defaultdict(list)
        for item in reactions:
            if (
                item.meta.available_time <= effective_cutoff
                and item.meta.trade_date == packet_trade_date
            ):
                reactions_by_symbol[item.meta.symbol_scope].append(item)
        packets = [
            self._assemble_loaded(
                symbol=symbol,
                trade_date=packet_trade_date,
                decision_time=decision_time,
                cutoff=effective_cutoff,
                record_source=record_source,
                packet_type=packet_type,
                snapshot_map=snapshot_map,
                events=events_by_symbol[symbol],
                reactions=reactions_by_symbol[symbol],
                include_history_ready_only=include_history_ready_only,
            )
            for symbol in values
        ]
        if persist:
            self.repository.save_feature_packets(packets)
        return packets

    def _assemble_loaded(
        self,
        *,
        symbol: str,
        trade_date: date,
        decision_time: datetime,
        cutoff: datetime,
        record_source: RecordSource,
        packet_type: PacketType,
        snapshot_map: dict[tuple[str, str], Any],
        events: list[EventIntelligenceRecord],
        reactions: list[EventReactionSnapshot],
        include_history_ready_only: bool,
    ) -> ResearchFeaturePacket:
        missing_modules: list[MissingModule] = []

        def select(kind: SnapshotType, scope: str, module: str) -> Any | None:
            item = snapshot_map.get((kind.value, scope))
            if item is None:
                missing_modules.append(
                    MissingModule(module=module, reason=MissingReason.PIT_UNAVAILABLE)
                )
                return None
            if item.meta.available_time > cutoff or item.meta.data_cutoff > cutoff:
                raise ValueError("bulk query returned a future snapshot")
            if item.meta.trade_date != trade_date:
                missing_modules.append(
                    MissingModule(
                        module=module,
                        reason=MissingReason.STALE,
                        detail=f"latest trade_date={item.meta.trade_date}",
                    )
                )
                return None
            return item

        market = select(SnapshotType.MARKET, "ALL_A", "market")
        speculation = select(SnapshotType.SPECULATION, "ALL_A", "speculation")
        stock = select(SnapshotType.STOCK, symbol, "stock")
        sector_id = None if stock is None else stock.industry_sector_id
        sector = None
        if sector_id:
            sector = select(SnapshotType.SECTOR, sector_id, "sector")
        else:
            missing_modules.append(
                MissingModule(
                    module="sector",
                    reason=MissingReason.NOT_APPLICABLE,
                    detail="stock has no PIT-visible industry membership",
                )
            )
        market_capital = select(SnapshotType.MARKET_CAPITAL, "ALL_A", "capital.market")
        stock_capital = select(SnapshotType.STOCK_CAPITAL, symbol, "capital.stock")
        sector_capital = None
        if sector_id:
            sector_capital = select(SnapshotType.SECTOR_CAPITAL, sector_id, "capital.sector")
        margin = select(SnapshotType.MARGIN_FINANCING, symbol, "capital.margin")
        dragon = select(SnapshotType.DRAGON_TIGER, symbol, "capital.dragon_tiger")
        news = select(SnapshotType.NEWS, symbol, "news")

        if margin is not None and margin.has_margin_data is None:
            missing_modules.append(
                MissingModule(module="capital.margin", reason=MissingReason.SOURCE_UNAVAILABLE)
            )
        if margin is not None and margin.has_margin_data is False:
            missing_modules = [item for item in missing_modules if item.module != "capital.margin"]
            missing_modules.append(
                MissingModule(module="capital.margin", reason=MissingReason.NO_MARGIN_ELIGIBILITY)
            )
        if dragon is not None and dragon.has_dragon_tiger is None:
            missing_modules.append(
                MissingModule(
                    module="capital.dragon_tiger", reason=MissingReason.SOURCE_UNAVAILABLE
                )
            )
        if dragon is not None and dragon.has_dragon_tiger is False:
            missing_modules = [
                item for item in missing_modules if item.module != "capital.dragon_tiger"
            ]
            missing_modules.append(
                MissingModule(module="capital.dragon_tiger", reason=MissingReason.NO_DRAGON_TIGER)
            )

        event_summary = self._event_summary(events)
        reaction_summary = self._reaction_summary(reactions)
        if not events:
            missing_modules.append(MissingModule(module="event", reason=MissingReason.NO_EVENT))
        if events and not reactions:
            missing_modules.append(
                MissingModule(module="reaction", reason=MissingReason.REACTION_NOT_AVAILABLE_YET)
            )
        elif not events:
            missing_modules.append(
                MissingModule(module="reaction", reason=MissingReason.NOT_APPLICABLE)
            )

        sources = {
            "MARKET": market,
            "SPECULATION": speculation,
            "SECTOR": sector,
            "STOCK": stock,
            "MARKET_CAPITAL": market_capital,
            "SECTOR_CAPITAL": sector_capital,
            "STOCK_CAPITAL": stock_capital,
            "MARGIN_FINANCING": margin,
            "DRAGON_TIGER": dragon,
            "NEWS": news,
        }
        definitions = (
            self.registry.history_safe()
            if include_history_ready_only
            else self.registry.canonical_definitions()
        )
        features: dict[str, Any] = {}
        feature_quality: dict[str, list[FeatureQuality]] = {}
        missing_reasons: dict[str, MissingReason] = {}
        lineage: dict[str, SnapshotLineage] = {}
        packet_flags: set[str] = set()
        for definition in definitions:
            value, source = self._feature_value(
                definition, sources, event_summary, reaction_summary
            )
            _nested_set(features, definition.feature_name, value)
            qualities: set[FeatureQuality] = set()
            if value is None:
                qualities.add(FeatureQuality.MISSING)
                reason = MissingReason.PIT_UNAVAILABLE
                if definition.source_snapshot_type == "DRAGON_TIGER" and dragon is not None:
                    reason = (
                        MissingReason.NO_DRAGON_TIGER
                        if dragon.has_dragon_tiger is False
                        else MissingReason.SOURCE_UNAVAILABLE
                    )
                if definition.source_snapshot_type == "MARGIN_FINANCING" and margin is not None:
                    reason = (
                        MissingReason.NO_MARGIN_ELIGIBILITY
                        if margin.has_margin_data is False
                        else MissingReason.SOURCE_UNAVAILABLE
                    )
                if definition.source_snapshot_type == "EVENT_REACTION" and events:
                    reason = MissingReason.REACTION_NOT_AVAILABLE_YET
                missing_reasons[definition.feature_name] = reason
            else:
                qualities.add(FeatureQuality.VALID)
            if ResearchStatus.VENDOR_DERIVED in definition.historical_support:
                qualities.add(FeatureQuality.VENDOR_DERIVED)
            if source is not None:
                meta = source.meta
                lineage[definition.feature_name] = _lineage(source)
                if meta.coverage_ratio < 1:
                    qualities.add(FeatureQuality.LOW_COVERAGE)
                if QualityFlag.STALE_DATA in meta.quality_flags:
                    qualities.add(FeatureQuality.STALE)
                if QualityFlag.PIT_UNSAFE in meta.quality_flags or (
                    QualityFlag.PIT_UNSAFE_FOR_HISTORY in meta.quality_flags
                ):
                    qualities.add(FeatureQuality.PIT_UNSAFE)
                if QualityFlag.AVAILABLE_TIME_INFERRED in meta.quality_flags:
                    qualities.add(FeatureQuality.INFERRED_TIME)
                packet_flags.update(item.value for item in meta.quality_flags)
            feature_quality[definition.feature_name] = sorted(qualities, key=str)

        universe_source = stock or stock_capital or margin or dragon
        universe_id = None if universe_source is None else universe_source.universe_id
        universe_version = None if universe_source is None else universe_source.universe_version
        coverage = {
            "market_coverage": None if market is None else market.meta.coverage_ratio,
            "sector_coverage": None if sector is None else sector.meta.coverage_ratio,
            "stock_coverage": None if stock is None else stock.meta.coverage_ratio,
            "capital_coverage": None
            if stock_capital is None
            else stock_capital.meta.coverage_ratio,
            "news_coverage": None if news is None else news.meta.coverage_ratio,
        }
        packet_payload = {
            "symbol": symbol,
            "trade_date": trade_date,
            "cutoff": cutoff,
            "record_source": record_source,
            "packet_type": packet_type,
            "universe_id": universe_id,
            "universe_version": universe_version,
            "snapshot_ids": {
                key: None if value is None else value.meta.snapshot_id
                for key, value in sources.items()
            },
            "event_ids": sorted(item.event_id for item in events),
            "reaction_ids": sorted(item.meta.snapshot_id for item in reactions),
            "feature_registry_version": self.registry.registry_version,
            "assembly_algorithm_version": ASSEMBLY_ALGORITHM_VERSION,
            "features": features,
            "feature_quality": feature_quality,
            "feature_missing_reasons": missing_reasons,
            "coverage": coverage,
        }
        digest = _packet_hash(packet_payload)
        return ResearchFeaturePacket(
            packet_id=f"sv3_packet_{digest[:32]}_{record_source.value.lower()}",
            packet_type=packet_type,
            symbol=symbol,
            trade_date=trade_date,
            decision_time=decision_time,
            cutoff=cutoff,
            record_source=record_source,
            universe_id=universe_id,
            universe_version=universe_version,
            market_snapshot_id=None if market is None else market.meta.snapshot_id,
            speculation_snapshot_id=None if speculation is None else speculation.meta.snapshot_id,
            sector_snapshot_id=None if sector is None else sector.meta.snapshot_id,
            stock_snapshot_id=None if stock is None else stock.meta.snapshot_id,
            market_capital_snapshot_id=(
                None if market_capital is None else market_capital.meta.snapshot_id
            ),
            sector_capital_snapshot_id=(
                None if sector_capital is None else sector_capital.meta.snapshot_id
            ),
            stock_capital_snapshot_id=(
                None if stock_capital is None else stock_capital.meta.snapshot_id
            ),
            margin_snapshot_id=None if margin is None else margin.meta.snapshot_id,
            dragon_tiger_snapshot_id=None if dragon is None else dragon.meta.snapshot_id,
            news_snapshot_id=None if news is None else news.meta.snapshot_id,
            event_ids=sorted(item.event_id for item in events),
            reaction_ids=sorted(item.meta.snapshot_id for item in reactions),
            feature_schema_version=self.registry.registry_version,
            assembly_algorithm_version=ASSEMBLY_ALGORITHM_VERSION,
            pit_status=PITStatus.PIT_SAFE,
            quality_status=(
                PacketQualityStatus.PARTIAL if missing_modules else PacketQualityStatus.COMPLETE
            ),
            missing_modules=sorted(missing_modules, key=lambda item: item.module),
            quality_flags=sorted(packet_flags),
            features=features,
            feature_names=sorted(feature_quality),
            feature_quality=feature_quality,
            feature_missing_reasons=missing_reasons,
            feature_lineage=lineage,
            coverage=coverage,
            created_at=datetime.now(tz=SHANGHAI),
            content_hash=digest,
        )

    @staticmethod
    def _feature_value(
        definition: FeatureDefinition,
        sources: dict[str, Any],
        event_summary: dict[str, Any],
        reaction_summary: dict[str, Any],
    ) -> tuple[Any, Any | None]:
        if definition.source_snapshot_type == "EVENT_INTELLIGENCE":
            return event_summary.get(definition.source_field), None
        if definition.source_snapshot_type == "EVENT_REACTION":
            return reaction_summary.get(definition.source_field), None
        source = sources.get(definition.source_snapshot_type)
        if source is None:
            return None, None
        if definition.source_field == "coverage_ratio":
            return source.meta.coverage_ratio, source
        return getattr(source, definition.source_field, None), source

    @staticmethod
    def _event_summary(events: list[EventIntelligenceRecord]) -> dict[str, Any]:
        directions = Counter(item.direction.value for item in events)
        return {
            "event_count": len(events),
            "new_event_count": sum(item.novelty == Novelty.NEW for item in events),
            "risk_event_count": sum(item.risk_event_candidate for item in events),
            "official_confirmed_count": sum(item.official_confirmation_exists for item in events),
            "positive_count": directions[TextDirection.POSITIVE.value],
            "negative_count": directions[TextDirection.NEGATIVE.value],
            "mixed_count": directions[TextDirection.MIXED.value],
            "event_type_counts": dict(
                sorted(Counter(item.event_type.value for item in events).items())
            ),
        }

    @staticmethod
    def _reaction_summary(reactions: list[EventReactionSnapshot]) -> dict[str, Any]:
        if not reactions:
            return {"reaction_count": 0}
        latest = max(reactions, key=lambda item: (item.reaction_end, item.meta.available_time))
        fields = {
            "stock_return",
            "market_return",
            "sector_return",
            "relative_market_return",
            "relative_sector_return",
            "volume_change",
            "turnover_change",
            "close_location_value",
            "open_gap_pct",
            "high_to_close_drawdown",
            "low_to_close_recovery",
        }
        return {
            "reaction_count": len(reactions),
            **{field: getattr(latest, field) for field in fields},
        }


class ResearchDatasetBuilder:
    def __init__(self, *, assembler: ResearchFeatureAssembler) -> None:
        self.assembler = assembler

    def build(
        self,
        *,
        symbols: Iterable[str],
        date_from: date,
        date_to: date,
        cutoff_policy: DatasetCutoffPolicy,
        record_source: RecordSource,
        feature_modules: Iterable[str] | None = None,
        include_history_ready_only: bool = False,
        persist_packets: bool = False,
        persist_dataset: bool = False,
        now: datetime | None = None,
    ) -> ResearchDataset:
        if date_to < date_from:
            raise ValueError("date_to cannot precede date_from")
        symbol_values = sorted(set(symbols))
        module_values = None if feature_modules is None else set(feature_modules)
        packets: list[ResearchFeaturePacket] = []
        current = date_from
        while current <= date_to:
            if cutoff_policy == DatasetCutoffPolicy.EOD:
                cutoff = datetime.combine(current, time(23, 59, 59), tzinfo=SHANGHAI)
                packet_type = PacketType.EOD_RESEARCH_PACKET
            else:
                effective_now = now or datetime.now(tz=SHANGHAI)
                if current != effective_now.astimezone(SHANGHAI).date() or date_from != date_to:
                    raise ValueError("FORWARD_SHADOW only supports the actual current session")
                cutoff = effective_now
                packet_type = PacketType.FORWARD_INTRADAY_PACKET
            packets.extend(
                self.assembler.assemble_many(
                    symbols=symbol_values,
                    decision_time=cutoff,
                    cutoff=cutoff,
                    trade_date=current,
                    record_source=record_source,
                    packet_type=packet_type,
                    include_history_ready_only=include_history_ready_only,
                    persist=persist_packets,
                )
            )
            current += timedelta(days=1)

        allowed = {
            item.feature_name
            for item in (
                self.assembler.registry.history_safe()
                if include_history_ready_only
                else self.assembler.registry.canonical_definitions()
            )
            if module_values is None
            or any(
                item.feature_name == module or item.feature_name.startswith(f"{module}.")
                for module in module_values
            )
        }
        rows: list[ResearchFeatureRow] = []
        for packet in packets:
            flat = packet.flat_features()
            selected = {key: flat.get(key) for key in sorted(allowed)}
            quality = {
                key: packet.feature_quality[key]
                for key in sorted(allowed)
                if key in packet.feature_quality
            }
            rows.append(
                ResearchFeatureRow(
                    packet_id=packet.packet_id,
                    symbol=packet.symbol,
                    trade_date=packet.trade_date,
                    cutoff=packet.cutoff,
                    record_source=packet.record_source,
                    universe_version=packet.universe_version,
                    features=selected,
                    feature_quality=quality,
                )
            )
        definition_payload = {
            "dataset_version": DATASET_VERSION,
            "feature_registry_version": self.assembler.registry.registry_version,
            "assembly_algorithm_version": ASSEMBLY_ALGORITHM_VERSION,
            "date_from": date_from,
            "date_to": date_to,
            "cutoff_policy": cutoff_policy,
            "record_source": record_source,
            "include_history_ready_only": include_history_ready_only,
            "feature_list": sorted(allowed),
            "packet_ids": sorted(item.packet_id for item in packets),
            "universe_versions": sorted(
                {item.universe_version for item in packets if item.universe_version}
            ),
        }
        digest = _packet_hash(definition_payload)
        definition = ResearchDatasetDefinition(
            dataset_id=f"sv3_dataset_{digest[:32]}_{record_source.value.lower()}",
            date_from=date_from,
            date_to=date_to,
            cutoff_policy=cutoff_policy,
            universe_versions=definition_payload["universe_versions"],
            feature_list=sorted(allowed),
            row_count=len(rows),
            symbol_count=len({item.symbol for item in rows}),
            record_source=record_source,
            include_history_ready_only=include_history_ready_only,
            packet_ids=definition_payload["packet_ids"],
            created_at=datetime.now(tz=SHANGHAI),
            content_hash=digest,
        )
        if persist_dataset:
            self.assembler.repository.save_research_dataset(definition)
        return ResearchDataset(definition=definition, rows=rows)


class SentimentV3ResearchShadow:
    """Future observation writer; deliberately has no action or execution API."""

    def __init__(self, *, assembler: ResearchFeatureAssembler) -> None:
        self.assembler = assembler

    def observe(
        self,
        *,
        symbols: Iterable[str],
        cutoff: datetime,
        packet_type: PacketType = PacketType.FORWARD_INTRADAY_PACKET,
        record_source: RecordSource = RecordSource.REAL,
    ) -> list[ResearchFeaturePacket]:
        return self.assembler.assemble_many(
            symbols=symbols,
            decision_time=cutoff,
            cutoff=cutoff,
            record_source=record_source,
            packet_type=packet_type,
            persist=True,
        )


__all__ = [
    "DatasetCutoffPolicy",
    "FeatureQuality",
    "MissingModule",
    "MissingReason",
    "PITStatus",
    "PacketQualityStatus",
    "PacketType",
    "ResearchDataset",
    "ResearchDatasetBuilder",
    "ResearchDatasetDefinition",
    "ResearchFeatureAssembler",
    "ResearchFeaturePacket",
    "ResearchFeatureRow",
    "SentimentV3ResearchShadow",
    "SnapshotLineage",
]
