"""Wealth Sentiment v1：独立的中国多资产资讯采集与 LLM 情绪研究模块。

与股票业务、S0-S8 策略、WealthRiskEngine、冻结契约完全隔离；
数据落独立库 database/wealth_sentiment.duckdb；
LLM 复用 Router（deepseek-flash 优先，qwen 兜底）。
"""
from trading.wealth_lab.sentiment.config import (  # noqa: F401
    SENTIMENT_DB_PATH, SENTIMENT_VERSION, INGEST_VERSION, PROMPT_VERSION,
    DEEPSEEK_SENTIMENT_MODEL, SOURCES, SYMBOL_CLASS,
)
