"""Sentiment V3 research-only snapshot data layer.

This package is intentionally disconnected from stock_report and all formal,
shadow-B, and canonical decision paths.
"""

from trading.research.sentiment.v3.capital import (
    CapitalCollector,
    DragonTigerCollector,
    MarginCollector,
)
from trading.research.sentiment.v3.capital_sources import (
    TushareCapitalSource,
    TushareDragonTigerSource,
    TushareMarginSource,
)
from trading.research.sentiment.v3.collectors import (
    AKShareSentimentV3Source,
    SentimentV3Collector,
)
from trading.research.sentiment.v3.feature_assembly import (
    DatasetCutoffPolicy,
    PacketType,
    ResearchDatasetBuilder,
    ResearchFeatureAssembler,
    ResearchFeaturePacket,
    SentimentV3ResearchShadow,
)
from trading.research.sentiment.v3.feature_registry import (
    FEATURE_REGISTRY_V1,
    FeatureDefinition,
    FeatureRegistry,
    ResearchStatus,
)
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.reaction import calculate_event_reaction
from trading.research.sentiment.v3.text_models import (
    EventIntelligenceRecord,
    EventIntelligenceSnapshot,
    EventReactionSnapshot,
    NewsSentimentRecord,
    NewsSentimentSnapshot,
)
from trading.research.sentiment.v3.text_pipeline import (
    TextIntelligenceProcessor,
    build_text_document,
    calculate_event_snapshot,
    calculate_news_snapshot,
)
from trading.research.sentiment.v3.sector_stock import (
    SectorStockSentimentCollector,
)
from trading.research.sentiment.v3.schemas import (
    DragonTigerSnapshot,
    Finality,
    MarginFinancingSnapshot,
    MarketCapitalSnapshot,
    MarketSentimentSnapshot,
    MarketUniverse,
    QualityFlag,
    RecordSource,
    SectorCapitalSnapshot,
    SectorSentimentSnapshot,
    SentimentSnapshotMeta,
    SnapshotType,
    StockCapitalSnapshot,
    StockTradingSentimentSnapshot,
    SpeculationSentimentSnapshot,
)

__all__ = [
    "SentimentV3ResearchShadow",
    "ResearchStatus",
    "ResearchFeaturePacket",
    "ResearchFeatureAssembler",
    "ResearchDatasetBuilder",
    "PacketType",
    "FeatureRegistry",
    "FeatureDefinition",
    "FEATURE_REGISTRY_V1",
    "DatasetCutoffPolicy",
    "calculate_news_snapshot",
    "calculate_event_snapshot",
    "calculate_event_reaction",
    "build_text_document",
    "TextIntelligenceProcessor",
    "NewsSentimentSnapshot",
    "NewsSentimentRecord",
    "EventReactionSnapshot",
    "EventIntelligenceSnapshot",
    "EventIntelligenceRecord",
    "AKShareSentimentV3Source",
    "CapitalCollector",
    "DragonTigerCollector",
    "DragonTigerSnapshot",
    "MarginCollector",
    "MarginFinancingSnapshot",
    "MarketCapitalSnapshot",
    "SectorCapitalSnapshot",
    "StockCapitalSnapshot",
    "TushareCapitalSource",
    "TushareDragonTigerSource",
    "TushareMarginSource",
    "Finality",
    "MarketSentimentSnapshot",
    "MarketUniverse",
    "QualityFlag",
    "RecordSource",
    "SentimentSnapshotMeta",
    "SectorSentimentSnapshot",
    "SectorStockSentimentCollector",
    "SentimentV3Collector",
    "SentimentV3Repository",
    "SnapshotType",
    "SpeculationSentimentSnapshot",
    "StockTradingSentimentSnapshot",
]
