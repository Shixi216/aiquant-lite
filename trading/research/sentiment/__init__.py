"""Sentiment research namespace reserved for source-attributed signals."""
from trading.research.sentiment.evaluation import (
    SentimentEvaluationService,
)
from trading.research.sentiment.schemas import (
    MarketBreadthSnapshot,
    SentimentAnalyzeRequest,
    SentimentAnalyzeResponse,
    SentimentEvaluation,
    SentimentEventAnalysis,
    SentimentEventType,
    SentimentRiskFlag,
    SentimentSymbolSnapshot,
)
from trading.research.sentiment.service import (
    DecisionSentimentService,
    ResearchSentimentService,
    ScreeningSentimentService,
    SentimentAnalysisService,
)

__all__ = [
    "DecisionSentimentService",
    "MarketBreadthSnapshot",
    "ResearchSentimentService",
    "ScreeningSentimentService",
    "SentimentAnalysisService",
    "SentimentAnalyzeRequest",
    "SentimentAnalyzeResponse",
    "SentimentEvaluation",
    "SentimentEvaluationService",
    "SentimentEventAnalysis",
    "SentimentEventType",
    "SentimentRiskFlag",
    "SentimentSymbolSnapshot",
]
