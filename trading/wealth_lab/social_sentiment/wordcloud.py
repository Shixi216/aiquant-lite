"""确定性词频（词云数据源）：真实评论分词统计，不经 LLM 生成频率。

- jieba 分词（MIT）；过滤停用词/纯数字/单字/URL/广告标记；
- 同批同文已由 content_hash 去重，另对同一词条在单条评论内只计一次
  （防刷屏重复灌水）；
- 金融主题词频按 FINANCE_TOPIC_TERMS 词典确定性归组；
- 输出附样本量与来源授权状态。
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from trading.wealth_lab.social_sentiment import repository as repo
from trading.wealth_lab.social_sentiment.config import (
    FINANCE_TOPIC_TERMS, STOPWORDS_ZH,
)

_TZ = timezone(__import__("datetime").timedelta(hours=8))
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_REPEAT_RE = re.compile(r"(.)\1{2,}")  # 3 连以上同字符视为灌水噪声


def _tokens(text: str) -> list[str]:
    import jieba
    cleaned = _URL_RE.sub(" ", text or "")
    cleaned = _REPEAT_RE.sub(r"\1", cleaned)
    out = []
    for token in jieba.lcut(cleaned):
        token = token.strip()
        if len(token) < 2 or token in STOPWORDS_ZH:
            continue
        if token.isdigit() or re.fullmatch(r"[\W_]+", token):
            continue
        if any(ch in token for ch in ("加群", "微信", "vx", "VX", "免费领")):
            continue
        out.append(token.lower())
    return out


def word_frequency(symbol: str | None = None, platform: str | None = None,
                   top_k: int = 50, include_synthetic: bool = False) -> dict:
    """评论词频 + 金融主题词频（确定性）。无真实数据时返回 INSUFFICIENT_DATA。"""
    comments = repo.all_comments(include_synthetic=include_synthetic)
    if symbol:
        comments = [c for c in comments if c.get("symbol") == symbol]
    if platform:
        comments = [c for c in comments if c["platform"] == platform]
    comments = [c for c in comments if c["data_origin"] != "synthetic_test"
                or include_synthetic]
    if not comments:
        return {"status": "INSUFFICIENT_DATA", "sample_size": 0,
                "message": "无真实、获准使用的评论（词云不由 LLM 生成频率）"}

    freq: Counter = Counter()
    topic_freq: Counter = Counter()
    topic_map = {term.lower(): topic for term, topic in FINANCE_TOPIC_TERMS}
    for comment in comments:
        tokens = set(_tokens(comment["content"]))  # 单条评论内每词只计一次（防刷屏）
        freq.update(tokens)
        for token in tokens:
            topic = topic_map.get(token)
            if topic:
                topic_freq[topic] += 1
        # 多字主题词直接子串匹配（"降息"等常被切成单字上下文时兜底）
        for term, topic in FINANCE_TOPIC_TERMS:
            if term.lower() in (comment["content"] or "").lower():
                topic_freq[topic] += 1

    from trading.wealth_lab.social_sentiment import registry
    source_ids = {c["source_id"] for c in comments}
    statuses = {s["source_id"]: s["status"] for s in registry.list_sources()}
    return {
        "status": "OK",
        "asof": datetime.now(_TZ).isoformat(),
        "symbol": symbol, "platform": platform,
        "word_frequency": freq.most_common(top_k),
        "finance_topic_frequency": topic_freq.most_common(),
        "sample_size": len(comments),
        "distinct_terms": len(freq),
        "source_authorization": {sid: statuses.get(sid, "UNKNOWN")
                                 for sid in sorted(source_ids)},
        "note": "词频为确定性分词统计（jieba + 停用词/广告过滤），非 LLM 生成",
    }
