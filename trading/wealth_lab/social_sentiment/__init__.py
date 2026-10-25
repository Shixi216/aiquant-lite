"""Wealth Social Sentiment：合规优先的金融社区舆情研究模块。

最高原则：任何未经授权的社区评论自动采集不得上线。
- Source Authorization Registry 是唯一闸门（PROHIBITED/UNVERIFIED/PENDING 默认关闭）；
- 数据独立落 database/wealth_social_sentiment.duckdb，与既有业务完全隔离；
- LLM 复用 Router：deepseek-flash 结构化分类，qwen 观点摘要；无自由辩论 Agent。
"""
from trading.wealth_lab.social_sentiment.config import (  # noqa: F401
    SOCIAL_DB_PATH, MODULE_VERSION, REGISTRY_SEED, STATUSES, OPERATIONS,
)
